"""Stage 7 audit log: append-only, hash-chained per tenant, managers only."""

import sqlite3

import pytest

from core.audit import AuditAccessDenied, AuditLog
from core.tenancy import MissingTenantContext, TenantContext

MGR = TenantContext(tenant_id="fjord", subject="worker-0001", roles=frozenset({"manager"}))
STAFF = TenantContext(tenant_id="fjord", subject="worker-0009")
OTHER_MGR = TenantContext(tenant_id="cinder", subject="worker-0001", roles=frozenset({"manager"}))


@pytest.fixture
def log():
    a = AuditLog(":memory:")
    for i in range(5):
        a.append(STAFF, "assistant.tool", {"tool": "query_data", "object": "ride", "rows": i})
    a.append(OTHER_MGR, "sign_in", {"name": "someone"})
    yield a
    a.close()


def test_entries_are_tenant_scoped_and_ordered(log):
    entries = log.entries(MGR)
    assert [e.seq for e in entries] == [5, 4, 3, 2, 1]  # newest first
    assert all(e.tenant_id == "fjord" and e.actor == "worker-0009" for e in entries)
    assert [e.action for e in log.entries(OTHER_MGR)] == ["sign_in"]


def test_reading_needs_manager(log):
    with pytest.raises(AuditAccessDenied):
        log.entries(STAFF)
    with pytest.raises(AuditAccessDenied):
        log.verify(STAFF)


def test_requires_tenant_context(log):
    with pytest.raises(MissingTenantContext):
        log.append(None, "x", {})


def test_chain_verifies(log):
    result = log.verify(MGR)
    assert result.ok and result.checked == 5 and result.first_bad is None


@pytest.mark.parametrize(
    "tamper",
    [
        'UPDATE audit_log SET detail = \'{"tool": "none"}\' '
        "WHERE tenant_id = 'fjord' AND seq = 3",
        "UPDATE audit_log SET actor = 'worker-0001' WHERE tenant_id = 'fjord' AND seq = 2",
        "DELETE FROM audit_log WHERE tenant_id = 'fjord' AND seq = 3",
        "UPDATE audit_log SET seq = 99 WHERE tenant_id = 'fjord' AND seq = 5",
    ],
)
def test_tampering_is_detected(log, tamper):
    db: sqlite3.Connection = log._db  # direct database access: the attacker the chain is for
    db.execute(tamper)
    db.commit()
    result = log.verify(MGR)
    assert not result.ok and result.first_bad is not None


def test_other_tenants_chain_is_independent(log):
    log._db.execute("DELETE FROM audit_log WHERE tenant_id = 'fjord'")
    assert log.verify(OTHER_MGR).ok


@pytest.mark.parametrize(
    "detail",
    [
        {"nested": {"a": 1}},
        {"long": "x" * 501},
        {"many": list(range(51))},
        {"bad key!": 1},
    ],
)
def test_detail_is_small_and_flat(log, detail):
    with pytest.raises(ValueError):
        log.append(STAFF, "x", detail)
