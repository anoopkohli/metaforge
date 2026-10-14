"""Stage 2 tool logic (no MCP SDK needed): threat-model entries 2.1-2.11."""

import json

import pytest

from core.tenancy import MissingTenantContext
from mcp_server.tools import (
    NAIVE_ENV,
    NAIVE_VALUE,
    TENANT_ARG_KEYS,
    Confirmation,
    TenantTools,
    ToolError,
    naive_mode_from_env,
)

VEHICLE = {
    "action": "create_object",
    "object": {"name": "vehicle", "fields": [{"name": "plate", "type": "string"}]},
}


@pytest.fixture
def env(seeded):
    store, tenants = seeded
    return store, TenantTools(store), tenants[0], tenants[1]


def propose(tools, tenant, proposal=VEHICLE) -> str:
    return tools.call(tenant.context(), "propose_change", {"proposal": proposal})["change_id"]


# ---- read tools -------------------------------------------------------------------------------


def test_list_objects_shows_platform_and_own_objects(env):
    _, tools, me, other = env
    result = tools.call(me.context(), "list_objects", {})
    names = {o["name"]: o["kind"] for o in result["objects"]}
    assert names["employee"] == "platform" and names[f"{me.tenant_id}_asset"] == "custom"
    assert f"{other.tenant_id}_asset" not in names


def test_describe_returns_effective_schema(env):
    _, tools, me, _ = env
    result = tools.call(me.context(), "describe_object", {"name": "employee"})
    names = [f["name"] for f in result["fields"]]
    assert "notes" not in names and "priority" in names  # fjord hid notes, added priority
    labels = {f["name"]: f["label"] for f in result["fields"]}
    assert labels["full_name"] == "Fjord Employee Name"


def test_not_found_message_is_identical_for_foreign_and_missing(env):
    _, tools, me, other = env
    with pytest.raises(ToolError) as foreign:
        tools.call(me.context(), "describe_object", {"name": f"{other.tenant_id}_asset"})
    with pytest.raises(ToolError) as missing:
        tools.call(me.context(), "describe_object", {"name": "zz_missing"})
    assert str(foreign.value).replace(f"{other.tenant_id}_asset", "X") == str(
        missing.value
    ).replace("zz_missing", "X")


def test_results_are_structured_json(env):
    _, tools, me, _ = env
    for name, args in [("list_objects", {}), ("describe_object", {"name": "employee"})]:
        result = tools.call(me.context(), name, args)
        assert json.loads(json.dumps(result)) == result


# ---- 2.1 / 2.2: arguments ---------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(TENANT_ARG_KEYS))
def test_tenant_args_are_ignored_and_audited(env, key):
    _, tools, me, other = env
    result = tools.call(me.context(), "list_objects", {key: other.tenant_id})
    assert f"{other.tenant_id}_asset" not in json.dumps(result)
    assert f"{me.tenant_id}_asset" in json.dumps(result)
    event = tools.audit[-1]
    assert event.tenant_id == me.tenant_id and event.ignored_args == (key,)


@pytest.mark.parametrize(
    "name,args",
    [
        ("list_objects", {"limit": 5}),
        ("describe_object", {"name": "employee", "include_hidden": True}),
        ("describe_object", {"name": 5}),
        ("describe_object", {}),
        ("propose_change", {"proposal": VEHICLE, "apply_now": True}),
        ("apply_change", {"change_id": "x", "confirmed": True}),
    ],
)
def test_unknown_arguments_are_rejected(env, name, args):
    _, tools, me, _ = env
    with pytest.raises(ToolError):
        tools.call(me.context(), name, args)


def test_unknown_tool_is_rejected(env):
    _, tools, me, _ = env
    with pytest.raises(ToolError):
        tools.call(me.context(), "drop_tables", {})


def test_requires_tenant_context(env):
    _, tools, _, _ = env
    with pytest.raises(MissingTenantContext):
        tools.call(None, "list_objects", {})


# ---- 2.5 / 2.7 / 2.8: writes ------------------------------------------------------------------


def test_propose_does_not_write(env):
    store, tools, me, _ = env
    result = tools.call(me.context(), "propose_change", {"proposal": VEHICLE})
    assert result["change_id"] and "vehicle" in result["summary"]
    assert store.get_object(me.context(), "vehicle") is None


def test_propose_reports_validation_errors_without_parking(env):
    _, tools, me, _ = env
    bad = {**VEHICLE, "object": {**VEHICLE["object"], "name": "employee"}}
    result = tools.call(me.context(), "propose_change", {"proposal": bad})
    assert result["accepted"] is False and "override_object" in result["errors"][0]
    assert "change_id" not in result


def test_propose_rejects_refusals(env):
    _, tools, me, _ = env
    result = tools.call(
        me.context(), "propose_change", {"proposal": {"action": "refuse", "reason": "no"}}
    )
    assert result["accepted"] is False


def test_apply_requires_matching_confirmation(env):
    store, tools, me, _ = env
    change_id = propose(tools, me)
    with pytest.raises(ToolError, match="confirmation"):
        tools.call(me.context(), "apply_change", {"change_id": change_id})
    with pytest.raises(ToolError, match="confirmation"):
        tools.call(
            me.context(), "apply_change", {"change_id": change_id}, confirmation=Confirmation("x")
        )
    assert store.get_object(me.context(), "vehicle") is None
    result = tools.call(
        me.context(), "apply_change", {"change_id": change_id}, confirmation=Confirmation(change_id)
    )
    assert result["applied"] is True
    assert store.get_object(me.context(), "vehicle") is not None


def test_change_ids_are_single_use_and_tenant_scoped(env):
    store, tools, me, other = env
    change_id = propose(tools, other)
    with pytest.raises(ToolError, match="no pending change"):
        tools.call(
            me.context(),
            "apply_change",
            {"change_id": change_id},
            confirmation=Confirmation(change_id),
        )
    ok = tools.call(
        other.context(),
        "apply_change",
        {"change_id": change_id},
        confirmation=Confirmation(change_id),
    )
    assert ok["applied"]
    with pytest.raises(ToolError, match="no pending change"):
        tools.call(
            other.context(),
            "apply_change",
            {"change_id": change_id},
            confirmation=Confirmation(change_id),
        )


def test_pending_changes_expire(seeded):
    store, tenants = seeded
    now = [1000.0]
    tools = TenantTools(store, ttl_s=60, clock=lambda: now[0])
    change_id = propose(tools, tenants[0])
    now[0] += 61
    with pytest.raises(ToolError, match="no pending change"):
        tools.call(
            tenants[0].context(),
            "apply_change",
            {"change_id": change_id},
            confirmation=Confirmation(change_id),
        )


def test_apply_revalidates(env):
    store, tools, me, _ = env
    change_id = propose(tools, me)
    from core.store import ObjectDef

    store.put_object(me.context(), ObjectDef.model_validate(VEHICLE["object"]))  # state changed
    with pytest.raises(ToolError, match="already exists"):
        tools.call(
            me.context(),
            "apply_change",
            {"change_id": change_id},
            confirmation=Confirmation(change_id),
        )


def test_pending_changes_are_capped(seeded):
    store, tenants = seeded
    tools = TenantTools(store, max_pending=3)
    for i in range(3):
        propose(tools, tenants[0], {**VEHICLE, "object": {**VEHICLE["object"], "name": f"v{i}"}})
    with pytest.raises(ToolError, match="too many pending"):
        propose(tools, tenants[0])
    propose(tools, tenants[1])  # another tenant's quota is separate


def test_pending_summary_is_tenant_scoped(env):
    _, tools, me, other = env
    change_id = propose(tools, me)
    assert "vehicle" in tools.pending_summary(me.context(), change_id)
    assert tools.pending_summary(other.context(), change_id) is None


# ---- 2.11: naive mode -------------------------------------------------------------------------


def test_naive_mode_is_off_by_default(seeded, monkeypatch):
    monkeypatch.delenv(NAIVE_ENV, raising=False)
    store, _ = seeded
    assert naive_mode_from_env() is False
    assert TenantTools(store).naive is False


@pytest.mark.parametrize("value", ["1", "true", "yes", NAIVE_VALUE.upper(), NAIVE_VALUE + " "])
def test_naive_mode_needs_the_exact_env_value(seeded, monkeypatch, value):
    store, _ = seeded
    monkeypatch.setenv(NAIVE_ENV, value)
    assert naive_mode_from_env() is False
    with pytest.raises(RuntimeError):
        TenantTools(store, naive=True)


def test_naive_mode_trusts_the_model_and_says_so(seeded, monkeypatch, caplog):
    store, tenants = seeded
    monkeypatch.setenv(NAIVE_ENV, NAIVE_VALUE)
    tools = TenantTools(store, naive=True)
    result = tools.call(tenants[0].context(), "list_objects", {"tenant_id": tenants[1].tenant_id})
    assert f"{tenants[1].tenant_id}_asset" in json.dumps(result)  # the deliberate vulnerability
    assert "NAIVE" in caplog.text
