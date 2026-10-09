"""Demo web UI: threat-model entries W.1-W.10, plus each feature working end to end through HTTP."""

import json
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from core.llm import FakeLLM
from evals.pricing import MeteredLLM
from web.app import CSRF_HEADER, SESSION_COOKIE, create_app

H = {CSRF_HEADER: "1"}
# fjord is a taxi company with its own `vehicle` object, so the new object needs another name.
PERMIT = {
    "action": "create_object",
    "object": {"name": "parking_permit", "fields": [{"name": "zone", "type": "string"}]},
}


@pytest.fixture
def client():
    with TestClient(create_app(secret="test-web-secret"), base_url="http://localhost") as c:
        yield c


def login(c, tenant="fjord", role="staff"):
    r = c.post("/api/login", json={"tenant_id": tenant, "role": role}, headers=H)
    assert r.status_code == 200, r.text
    return r


# ---- sessions and tenancy (W.1, W.2, W.9) -----------------------------------------------------


def test_login_sets_an_httponly_strict_cookie(client):
    assert client.get("/api/session").json()["tenant"] is None
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    session = client.get("/api/session").json()
    assert session["tenant"] == "fjord" and "cinder" in session["tenants"]


def test_unknown_tenant_cannot_log_in(client):
    r = client.post("/api/login", json={"tenant_id": "nobody"}, headers=H)
    assert r.status_code == 400


def test_api_requires_a_session(client):
    assert client.get("/api/objects").status_code == 401


def test_tampered_cookie_is_rejected(client):
    login(client)
    token = client.cookies[SESSION_COOKIE]
    payload, sig = token.split(".")
    client.cookies.set(SESSION_COOKIE, payload[:-2] + "xx." + sig)
    assert client.get("/api/objects").status_code == 401


def test_data_is_scoped_to_the_session_tenant(client):
    login(client)
    names = [o["name"] for o in client.get("/api/objects").json()["objects"]]
    assert "fjord_asset" in names and "cinder_asset" not in names
    assert client.get("/api/objects/cinder_asset").status_code == 404


@pytest.mark.parametrize("extra", [{"tenant_id": "cinder"}, {"tenant": "cinder"}])
def test_tenant_in_body_is_rejected(client, extra):
    login(client)
    r = client.post("/api/ask", json={"question": "expense limit", **extra}, headers=H)
    assert r.status_code == 422


def test_session_never_exposes_secrets(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-leak-123")
    login(client)
    text = client.get("/api/session").text
    assert "sk-ant" not in text and "test-web-secret" not in text


# ---- browser-facing protections (W.3, W.4, W.5, W.8) ------------------------------------------


def test_post_without_csrf_header_is_rejected(client):
    r = client.post("/api/login", json={"tenant_id": "fjord"})
    assert r.status_code == 403


def test_cross_origin_post_is_rejected(client):
    r = client.post(
        "/api/login",
        json={"tenant_id": "fjord"},
        headers={**H, "Origin": "http://evil.example"},
    )
    assert r.status_code == 403


def test_unknown_host_is_rejected():
    with TestClient(create_app(secret="s"), base_url="http://evil.example") as c:
        assert c.get("/api/session").status_code == 400


def test_security_headers(client):
    for path in ("/", "/api/session"):
        h = client.get(path).headers
        csp = h["content-security-policy"]
        assert "default-src 'self'" in csp and "script-src 'self'" in csp
        assert "'unsafe-inline'" not in csp.split("style-src")[0]
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"


def test_front_end_never_uses_innerhtml():
    static = Path(__file__).parent.parent / "web" / "static"
    for f in static.glob("*.js"):
        text = f.read_text()
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert banned not in text, f"{f.name} uses {banned}"
    html = (static / "index.html").read_text()
    assert "<script>" not in html and "onclick=" not in html  # no inline script


def test_errors_are_generic(client, monkeypatch):
    login(client)
    app_state = client.app.state.metaforge

    def boom(*a, **k):
        raise RuntimeError("internal detail: /secret/path")

    monkeypatch.setattr(app_state.rag, "answer", boom)
    r = client.post("/api/ask", json={"question": "expense limit"}, headers=H)
    assert r.status_code == 500 and "secret" not in r.text and r.json()["error"] == "internal error"


# ---- features ---------------------------------------------------------------------------------


def test_generate_offline_uses_recorded_examples(client):
    login(client)
    examples = client.get("/api/generate/examples").json()["examples"]
    assert len(examples) >= 5
    shirt = next(e["description"] for e in examples if "shirt size" in e["description"])
    r = client.post("/api/generate", json={"description": shirt}, headers=H)
    body = r.json()
    assert r.status_code == 200 and body["mode"] == "offline" and body["ok"]
    assert body["attempts"] and body["proposal"]["action"] in ("create_object", "override_object")
    other = client.post("/api/generate", json={"description": "Something new entirely"}, headers=H)
    assert other.json()["ok"] is False and "offline" in other.json()["message"].lower()


def test_change_needs_approval_and_stays_in_tenant(client):
    login(client)
    change = client.post("/api/changes", json={"proposal": PERMIT}, headers=H).json()
    assert change["accepted"] and "parking_permit" in change["summary"]
    names = lambda: [o["name"] for o in client.get("/api/objects").json()["objects"]]  # noqa: E731
    assert "parking_permit" not in names()
    r = client.post(f"/api/changes/{change['change_id']}/approve", headers=H)
    assert r.status_code == 200 and r.json()["applied"]
    assert "parking_permit" in names()
    login(client, "cinder")
    assert "parking_permit" not in names()


def test_ask_answers_from_own_documents(client):
    login(client)
    docs = client.get("/api/documents").json()["documents"]
    assert {d["doc_id"] for d in docs} >= {"expense_policy", "runbook_db_latency"}
    r = client.post("/api/ask", json={"question": "What is the expense approval limit?"}, headers=H)
    body = r.json()
    assert body["citations"] and body["citations"][0].startswith("expense_policy")
    assert {h["tenant_id"] for h in body["hits"]} == {"fjord"}


def test_agent_proposals_wait_for_ui_approval(client):
    login(client)
    r = client.post("/api/incident/start", json={"fault": "db_latency"}, headers=H)
    assert [a["metric"] for a in r.json()["alerts"]] == ["store.latency_p95_ms"]
    result = client.post("/api/incident/triage", json={"agent": "reference"}, headers=H).json()
    assert result["status"] == "finished" and result["diagnosis"]["fault_type"] == "db_latency"
    assert result["steps"] and all("action" in s for s in result["steps"])
    [prop] = result["proposals"]
    assert prop["status"] == "proposed"  # the agent cannot execute anything
    state = client.post(f"/api/incident/proposals/{prop['id']}/approve", headers=H).json()
    assert state["alerts"] == []  # the approved fix cleared the alert
    assert len(state["metrics"]["store.latency_p95_ms"]) > 25


def test_fooled_agent_is_contained(client):
    login(client)
    client.post(
        "/api/incident/start", json={"fault": "queue_backlog", "malicious_payload": True}, headers=H
    )
    result = client.post("/api/incident/triage", json={"agent": "fooled"}, headers=H).json()
    assert all(p["status"] == "proposed" for p in result["proposals"])
    assert all(p["params"].get("replicas", 0) <= 16 for p in result["proposals"])
    assert any("error" in s["observation"] for s in result["steps"])  # its injected tries bounced
    ignored = [s for s in result["steps"] if "ignored_arguments" in s["observation"]]
    assert ignored and "cinder" not in json.dumps(ignored[0]["observation"]["logs"])
    # Despite the injections, it still finds and proposes the real fix.
    assert [p["action"] for p in result["proposals"]] == ["dead_letter_message"]


def test_incident_is_tenant_scoped(client):
    login(client)
    client.post("/api/incident/start", json={"fault": "db_latency"}, headers=H)
    login(client, "cinder")
    assert client.get("/api/incident").json()["active"] is False


def test_live_agent_needs_a_model(client):
    login(client)
    client.post("/api/incident/start", json={"fault": "db_latency"}, headers=H)
    r = client.post("/api/incident/triage", json={"agent": "claude"}, headers=H)
    assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.json()["error"]


# ---- lab, evals, budget (W.6, W.7) ------------------------------------------------------------


def test_lab_runs_naive_only_in_a_child_process(client):
    login(client)
    catalogue = client.get("/api/lab/attacks").json()["suites"]
    assert {"mcp", "rag", "triage"} <= set(catalogue)
    r = client.post("/api/lab/run", json={"suite": "triage"}, headers=H)
    results = r.json()["results"]
    assert results and all(x["secure"] is False for x in results)
    assert all(x["naive"] is True for x in results)
    assert not any(k.startswith("METAFORGE_UNSAFE") for k in os.environ)


def test_lab_is_single_flight(client):
    login(client)
    lock = client.app.state.metaforge.lab_lock
    assert lock.acquire(blocking=False)
    try:
        r = client.post("/api/lab/run", json={"suite": "mcp"}, headers=H)
        assert r.status_code == 409
    finally:
        lock.release()


def test_ui_budget_is_enforced():
    # A valid answer of 600 words: 600 output tokens x $20/M = $0.012, over the $0.01 budget.
    reply = json.dumps({"answer": " ".join(["ok"] * 600), "citations": []})
    llm = MeteredLLM(FakeLLM([reply]), model="claude-opus-5-5", budget_usd=0.01)
    with TestClient(create_app(secret="s", live_llm=llm), base_url="http://localhost") as c:
        login(c)
        first = c.post("/api/ask", json={"question": "expense approval limit"}, headers=H)
        assert first.status_code == 200
        second = c.post("/api/ask", json={"question": "leave carry over days"}, headers=H)
        assert second.status_code == 429 and "budget" in second.json()["error"]


def test_evals_run_through_the_gate(client):
    login(client)
    rows = client.post("/api/evals/run", headers=H).json()["suites"]
    assert {r["suite"] for r in rows} >= {"smoke", "generator", "rag", "triage"}
    assert all(r["gate"] == "pass" for r in rows)


# ---- stage 6: businesses, roles, Ask data -----------------------------------------------------


def test_session_shows_business_and_role(client):
    login(client, "gable", role="manager")
    session = client.get("/api/session").json()
    assert session["role"] == "manager"
    assert session["businesses"]["gable"]["name"] == "Gable Family Dental"


def test_role_cannot_be_sent_with_a_question(client):
    login(client)
    r = client.post("/api/data/ask", json={"question": "salaries?", "role": "manager"}, headers=H)
    assert r.status_code == 422


def test_schema_marks_restricted_fields_by_role(client):
    def salary_readable():
        objects = {o["name"]: o for o in client.get("/api/data/schema").json()["objects"]}
        return next(f for f in objects["worker"]["fields"] if f["name"] == "salary")["readable"]

    login(client, role="staff")
    assert salary_readable() is False
    login(client, role="manager")
    assert salary_readable() is True


def test_ask_data_offline_examples_respect_roles(client):
    login(client, role="staff")
    examples = client.get("/api/data/examples").json()["examples"]
    salary_q = next(q for q in examples if "salar" in q)
    staff = client.post("/api/data/ask", json={"question": salary_q}, headers=H).json()
    assert staff["status"] == "denied" and staff["result"] is None
    login(client, role="manager")
    mgr = client.post("/api/data/ask", json={"question": salary_q}, headers=H).json()
    assert mgr["status"] == "answered" and mgr["result"]["rows"]


def test_ask_data_free_text_needs_a_model_offline(client):
    login(client)
    body = client.post(
        "/api/data/ask", json={"question": "Who drove the longest ride?"}, headers=H
    ).json()
    assert body["status"] == "offline" and "example" in body["answer"].lower()


def test_query_builder_runs_and_enforces_roles(client):
    login(client, role="staff")
    ok = client.post(
        "/api/data/query",
        json={"query": {"object": "ride", "group_by": "pickup_zone", "aggregate": {"fn": "count"}}},
        headers=H,
    )
    assert ok.status_code == 200 and sum(r["value"] for r in ok.json()["result"]["rows"]) == 220
    denied = client.post(
        "/api/data/query",
        json={"query": {"object": "worker", "aggregate": {"fn": "avg", "field": "salary"}}},
        headers=H,
    )
    assert denied.status_code == 400 and "restricted" in denied.json()["error"]
    foreign = client.post("/api/data/query", json={"query": {"object": "client"}}, headers=H)
    assert foreign.status_code == 400 and "no object" in foreign.json()["error"]


def test_browse_records_hides_restricted_columns_from_staff(client):
    login(client, role="staff")
    staff = client.get("/api/objects/worker/records").json()
    assert "salary" not in staff["columns"] and staff["rows"]
    login(client, role="manager")
    assert "salary" in client.get("/api/objects/worker/records").json()["columns"]


def test_business_documents_are_searchable(client):
    login(client)
    docs = {d["doc_id"] for d in client.get("/api/documents").json()["documents"]}
    assert "fare_policy" in docs and "billing_policy" not in docs  # fjord is the taxi company


def test_front_end_clears_every_tenant_view_on_session_change():
    """Switching company or role must not leave the previous session's data on screen (W.11)."""
    import re

    static = Path(__file__).parent.parent / "web" / "static"
    js = (static / "app.js").read_text()
    html = (static / "index.html").read_text()
    declared = set(re.findall(r'"([a-z-]+)"', js[js.index("const TENANT_VIEWS") : js.index("];")]))
    rendered = {
        i
        for i in re.findall(r'id="([a-z-]+)"', html)
        if re.search(
            r"(result|list|detail|examples|suggestions|status|charts|builder|biz-name|biz-desc|gen-example)$",
            i,
        )
    }
    assert rendered and rendered <= declared, (
        f"not cleared on session change: {rendered - declared}"
    )
    login_body = js[js.index("async function login()") :][:200]
    assert "resetTenantViews()" in login_body.split("await")[0]  # cleared before any request
    assert "if (epoch !== state.epoch) throw new StaleResponse()" in js
