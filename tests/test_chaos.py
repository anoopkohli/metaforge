"""Stage 5 simulation: each fault is visible, confined to its tenant, and fixed by its remedy."""

import sys

import pytest

from chaos import FAULT_TYPES, ChaosEnvironment

REMEDY = {
    "db_latency": lambda env, tid, truth: env.set_pool_size(tid, env.state[tid].pool_size * 2),
    "queue_backlog": lambda env, tid, truth: env.dead_letter(tid, truth["message_id"]),
    "bad_metadata_change": lambda env, tid, truth: env.revert_metadata(
        tid, "employee", truth["to_version"]
    ),
}
ALERT = {
    "db_latency": "store.latency_p95_ms",
    "queue_backlog": "queue.backlog",
    "bad_metadata_change": "requests.error_rate",
}


@pytest.mark.parametrize("seed", [7, 11])
@pytest.mark.parametrize("kind", FAULT_TYPES)
@pytest.mark.parametrize("idx", [0, 1, 2])
def test_fault_alerts_only_its_tenant_and_the_remedy_clears_it(seed, kind, idx):
    env = ChaosEnvironment(seed=seed)
    env.run(15)
    tid = env.tenant_ids()[idx]
    assert all(env.alerts(t) == [] for t in env.tenant_ids())
    truth = env.inject(kind, tid)
    env.run(10)
    assert [a.metric for a in env.alerts(tid)] == [ALERT[kind]]
    assert all(env.alerts(t) == [] for t in env.tenant_ids() if t != tid)
    REMEDY[kind](env, tid, truth)
    env.run(12)
    assert env.alerts(tid) == []


@pytest.mark.parametrize("kind", FAULT_TYPES)
def test_wrong_remedy_does_not_clear_the_alert(kind):
    env = ChaosEnvironment()
    env.run(15)
    tid = env.tenant_ids()[0]
    env.inject(kind, tid)
    env.run(10)
    env.set_workers(tid, env.state[tid].workers * 2)  # plausible, but fixes none of the three
    env.run(12)
    assert [a.metric for a in env.alerts(tid)] == [ALERT[kind]]


def test_simulation_is_deterministic():
    def snapshot():
        env = ChaosEnvironment(seed=7)
        env.run(10)
        env.inject("queue_backlog", env.tenant_ids()[1])
        env.run(10)
        return [(line.id, line.message) for line in env.logs]

    assert snapshot() == snapshot()


def test_runbook_facts_match_the_simulation():
    from core.synthetic import synthetic_documents

    env = ChaosEnvironment()
    for t in env.tenants:
        facts = {k: v for d in synthetic_documents(t) for k, v in d.facts.items()}
        st = env.state[t.tenant_id]
        assert str(st.pool_size) == facts["pool_size"]
        assert str(st.backlog_threshold) == facts["backlog_threshold"]
        assert str(st.latency_threshold_ms) == facts["latency_threshold_ms"]


def test_chaos_has_no_global_side_effects():
    import time

    before = (time.sleep, time.time, dict(sys.modules["core.store.sqlite_store"].__dict__))
    env = ChaosEnvironment()
    for kind, tid in zip(FAULT_TYPES, env.tenant_ids(), strict=True):
        env.inject(kind, tid)
    env.run(30)
    after = (time.sleep, time.time, dict(sys.modules["core.store.sqlite_store"].__dict__))
    assert before == after
    assert ChaosEnvironment().alerts(env.tenant_ids()[0]) == []  # a fresh env is unaffected


def test_unknown_fault_is_rejected():
    with pytest.raises(ValueError):
        ChaosEnvironment().inject("disk_full", "fjord")
