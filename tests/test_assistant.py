"""Stage 7 assistant: tools, approvals and auditing (offline, scripted models)."""

import json

import pytest

from assistant.agent import run_assistant
from assistant.reference import scripted_policy
from assistant.tools import (
    MAX_STEPS,
    NAIVE_ENV,
    NAIVE_VALUE,
    AssistantError,
    AssistantTools,
    naive_assistant_enabled,
)
from attacks.assistant import ATTACKS, NAIVE_EXPECTED_TO_SUCCEED, run_assistant_attack, world
from core.business import directory, roles_for
from core.tenancy import MissingTenantContext, TenantContext


def plan(tool, **args):
    return {"tool": tool, "args": args}


def as_person(w, title, tenant="fjord"):
    t = w.tenants[tenant]
    p = next(p for p in directory(w.store, t) if p["title"] == title)
    rec = next(x for x in w.store.list_records(t.context(), "worker") if x["id"] == p["id"])
    return TenantContext(tenant, subject=p["id"], roles=frozenset(roles_for(tenant, rec)))


# ---- answering ---------------------------------------------------------------------------------


def test_multi_step_answer_with_sources():
    w = world(False)
    mgr = as_person(w, "Operations Manager")
    actions = [
        plan(
            "query_data",
            query={
                "object": "ride",
                "group_by": "pickup_zone",
                "aggregate": {"fn": "sum", "field": "fare"},
            },
        ),
        plan("search_documents", query="airport pickup access fee"),
        plan(
            "finish",
            answer="Airport fares lead; airport pickups add a 5 dollar fee.",
            sources=["query:1", "fare_policy#1"],
        ),
    ]
    res = run_assistant(
        scripted_policy(actions), w.tools, mgr, "Fares by zone and the airport fee?"
    )
    assert res.status == "finished" and len(res.steps) == 3
    assert res.sources == ["query:1", "fare_policy#1"]


def test_unknown_sources_are_rejected():
    w = world(False)
    actions = [plan("finish", answer="Trust me.", sources=["fare_policy#0", "query:7"])]
    res = run_assistant(scripted_policy(actions), w.tools, w.ctx, "anything", max_steps=3)
    assert "fare_policy#0" in res.steps[0]["observation"]["error"]
    assert res.answer != "Trust me."  # the fabricated citation never became the answer


def test_identity_arguments_are_rejected():
    w = world(False)
    for extra in ({"tenant_id": "cinder"}, {"as_user": "worker-0001"}, {"role": "manager"}):
        with pytest.raises(AssistantError):
            w.tools.call(w.ctx, "list_data", extra)


def test_step_cap():
    w = world(False)
    res = run_assistant(scripted_policy([plan("list_data")] * 30), w.tools, w.ctx, "loop")
    assert res.status == "step_limit" and len(res.steps) == MAX_STEPS


def test_requires_tenant_context():
    w = world(False)
    with pytest.raises(MissingTenantContext):
        w.tools.call(None, "list_data", {})


# ---- proposals and approvals ------------------------------------------------------------------


def test_record_update_waits_for_approval_and_rechecks():
    w = world(False)
    fjord = w.tenants["fjord"].context()
    vehicle = w.store.list_records(fjord, "vehicle")[0]
    prop = w.tools.call(
        w.ctx,
        "propose_record_update",
        {"object": "vehicle", "record_id": vehicle["id"], "changes": {"status": "retired"}},
    )
    assert prop["status"] == "proposed"
    assert w.store.list_records(fjord, "vehicle")[0]["status"] == vehicle["status"]
    other = (
        as_person(w, "Driver", tenant="cinder")
        if False
        else TenantContext("cinder", subject="worker-0001")
    )
    with pytest.raises(AssistantError):
        w.tools.approve(other, prop["proposal_id"])  # another tenant cannot approve it
    done = w.tools.approve(w.ctx, prop["proposal_id"])
    assert done["status"] == "executed"
    assert w.store.list_records(fjord, "vehicle")[0]["status"] == "retired"
    with pytest.raises(AssistantError):
        w.tools.approve(w.ctx, prop["proposal_id"])  # single use


@pytest.mark.parametrize(
    "changes",
    [{"salary": 1.0}, {"no_such_field": 1}, {"model_year": "new"}, {"id": "x"}, {}],
)
def test_bad_updates_are_rejected(changes):
    w = world(False)
    rec = w.store.list_records(w.tenants["fjord"].context(), "vehicle")[0]
    obj = "worker" if "salary" in changes else "vehicle"
    rid = w.driver["id"] if obj == "worker" else rec["id"]
    with pytest.raises(AssistantError):
        w.tools.call(
            w.ctx, "propose_record_update", {"object": obj, "record_id": rid, "changes": changes}
        )
    assert w.tools.all_proposals() == []


def test_reference_updates_must_point_inside_the_tenant():
    w = world(False)
    mgr = as_person(w, "Operations Manager")
    ride = w.store.list_records(w.tenants["fjord"].context(), "ride")[0]
    with pytest.raises(AssistantError):
        w.tools.call(
            mgr,
            "propose_record_update",
            {"object": "ride", "record_id": ride["id"], "changes": {"vehicle": "vehicle-9999"}},
        )


def test_schema_changes_need_a_manager():
    w = world(False)
    change = {
        "action": "override_object",
        "target": "worker",
        "override": {"add_fields": [{"name": "shirt_size", "type": "string"}]},
    }
    with pytest.raises(AssistantError, match="manager"):
        w.tools.call(w.ctx, "propose_schema_change", {"proposal": change})
    mgr = as_person(w, "Operations Manager")
    prop = w.tools.call(mgr, "propose_schema_change", {"proposal": change})
    w.tools.approve(mgr, prop["proposal_id"])
    names = [f.name for f in w.store.get_effective_schema(mgr, "worker").fields]
    assert "shirt_size" in names


# ---- auditing ---------------------------------------------------------------------------------


def test_every_step_is_audited():
    w = world(False)
    mgr = as_person(w, "Operations Manager")
    actions = [
        plan("list_data"),
        plan("query_data", query={"object": "worker", "select": ["salary"]}),
        plan("no_such_tool"),
    ]
    res = run_assistant(scripted_policy(actions), w.tools, w.ctx, "show salaries")
    entries = w.audit.entries(mgr)
    actions_logged = [e.action for e in reversed(entries)]
    assert actions_logged[0] == "assistant.question"
    assert actions_logged.count("assistant.tool") == len(res.steps) - (
        1 if res.status == "finished" else 0
    )
    denied = [
        e for e in entries if e.action == "assistant.tool" and e.detail.get("outcome") == "denied"
    ]
    assert denied and denied[0].detail["tool"] == "query_data"
    assert w.audit.verify(mgr).ok


def test_audit_never_stores_restricted_values():
    w = world(False)
    mgr = as_person(w, "Operations Manager")
    run_assistant(
        scripted_policy(
            [plan("query_data", query={"object": "worker", "select": ["full_name", "salary"]})]
        ),
        w.tools,
        mgr,
        "salaries",
    )
    text = json.dumps([e.to_dict() for e in w.audit.entries(mgr)])
    salaries = {
        json.dumps(x["salary"])
        for x in w.store.list_records(w.tenants["fjord"].context(), "worker")
    }
    assert not any(s in text for s in salaries)


def test_approvals_are_audited():
    w = world(False)
    mgr = as_person(w, "Operations Manager")
    vehicle = w.store.list_records(w.tenants["fjord"].context(), "vehicle")[0]["id"]
    prop = w.tools.call(
        w.ctx,
        "propose_record_update",
        {"object": "vehicle", "record_id": vehicle, "changes": {"status": "active"}},
    )
    w.tools.approve(w.ctx, prop["proposal_id"])
    actions = [e.action for e in w.audit.entries(mgr)]
    assert "proposal.created" in actions and "proposal.executed" in actions


# ---- attacks and naive mode -------------------------------------------------------------------


@pytest.mark.parametrize("attack_id", sorted(ATTACKS))
def test_attack_is_blocked_in_secure_mode(attack_id, monkeypatch):
    monkeypatch.delenv(NAIVE_ENV, raising=False)
    assert run_assistant_attack(attack_id) is False


@pytest.mark.parametrize("attack_id", sorted(ATTACKS))
def test_naive_mode_outcome_matches_the_claim(attack_id, monkeypatch):
    monkeypatch.setenv(NAIVE_ENV, NAIVE_VALUE)
    monkeypatch.setenv("METAFORGE_UNSAFE_NAIVE_RAG", "disable-tenant-filters")
    assert run_assistant_attack(attack_id, naive=True) is (attack_id in NAIVE_EXPECTED_TO_SUCCEED)


def test_assistant_naive_mode_is_off_by_default(monkeypatch):
    monkeypatch.delenv(NAIVE_ENV, raising=False)
    assert naive_assistant_enabled() is False
    with pytest.raises(RuntimeError):
        world(False).tools.__class__(None, None, None, naive=True)


@pytest.mark.parametrize("value", ["1", "true", NAIVE_VALUE.upper(), NAIVE_VALUE + "x"])
def test_assistant_naive_mode_needs_the_exact_env_value(monkeypatch, value):
    monkeypatch.setenv(NAIVE_ENV, value)
    assert naive_assistant_enabled() is False


def test_tools_class_is_exported():
    assert AssistantTools.__name__ == "AssistantTools"


def test_approval_rechecks_the_approvers_rights():
    """A manager's proposal to change a salary cannot be approved by staff."""
    w = world(False)
    mgr = as_person(w, "Operations Manager")
    prop = w.tools.call(
        mgr,
        "propose_record_update",
        {"object": "worker", "record_id": w.driver["id"], "changes": {"salary": 60000.0}},
    )
    with pytest.raises(AssistantError, match="restricted"):
        w.tools.approve(w.ctx, prop["proposal_id"])  # the driver (staff)
    assert w.tools.approve(mgr, prop["proposal_id"])["status"] == "executed"
