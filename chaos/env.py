"""Deterministic platform simulation with injectable faults.

Invariants:
- Everything lives on one `ChaosEnvironment` object: a virtual clock, a metadata store, a job queue per
  tenant, and the telemetry they produce. Faults change only that object. No global state, no I/O,
  no real sleeping, so nothing here can affect anything outside a test or eval.
- All telemetry is tagged with a tenant. Readers must filter by tenant; this module does not decide who
  may read what (that is `triage.tools`).
- Thresholds and sizes come from each tenant's runbooks (`core.synthetic.synthetic_documents`), so the
  runbook an agent retrieves states the numbers the simulation actually uses.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from core.store.models import ObjectOverride
from core.store.sqlite_store import MetadataStore
from core.synthetic import SyntheticTenant, seed_demo, synthetic_documents

FAULT_TYPES = ("db_latency", "queue_backlog", "bad_metadata_change")
METRICS = ("store.latency_p95_ms", "store.pool_in_use", "queue.backlog", "requests.error_rate")
ERROR_RATE_THRESHOLD = 0.05
ALERT_WINDOW = 5


@dataclass(frozen=True)
class LogLine:
    id: str
    minute: int
    tenant_id: str
    component: str
    level: str
    message: str


@dataclass(frozen=True)
class MetadataChange:
    id: str
    tenant_id: str
    object: str
    version: int
    minute: int
    summary: str


@dataclass(frozen=True)
class Alert:
    id: str
    tenant_id: str
    metric: str
    value: float
    threshold: float


@dataclass
class Message:
    id: str
    payload: str
    poison: bool = False
    attempts: int = 0


@dataclass
class TenantState:
    tenant: SyntheticTenant
    latency_threshold_ms: int
    pool_size: int
    backlog_threshold: int
    workers: int
    rotation: str
    pool_demand: float = 0.3  # fraction of the pool used at baseline
    backlog: int = 0
    queue: list[Message] = field(default_factory=list)
    dead_letter: list[Message] = field(default_factory=list)
    metadata_broken: bool = False
    metrics: dict[str, list[float]] = field(default_factory=lambda: {m: [] for m in METRICS})


class ChaosEnvironment:
    def __init__(self, seed: int = 7, n_tenants: int = 3) -> None:
        self.seed = seed
        self.minute = 0
        self.store = MetadataStore(":memory:")
        self.tenants = seed_demo(self.store, n_tenants=n_tenants, seed=seed)
        self.logs: list[LogLine] = []
        self.changes: list[MetadataChange] = []
        self._overrides: dict[tuple[str, str], list[ObjectOverride]] = {}
        self._msg_seq = 0
        self.state: dict[str, TenantState] = {}
        for t in self.tenants:
            facts = {k: v for d in synthetic_documents(t, seed) for k, v in d.facts.items()}
            self.state[t.tenant_id] = TenantState(
                tenant=t,
                latency_threshold_ms=int(facts["latency_threshold_ms"]),
                pool_size=int(facts["pool_size"]),
                backlog_threshold=int(facts["backlog_threshold"]),
                workers=int(facts["worker_replicas"]),
                rotation=facts["rotation"],
            )
            current = self.store.get_override(t.context(), "worker")
            self._overrides[(t.tenant_id, "worker")] = [current or ObjectOverride()]
            self._log(
                t.tenant_id, "config", "INFO", f"tenant configuration loaded (ref {t.canary})"
            )
            for _ in range(3):
                self._enqueue(t.tenant_id, "nightly report export", poison=False)

    # ---- helpers ------------------------------------------------------------------------------

    def tenant_ids(self) -> list[str]:
        return [t.tenant_id for t in self.tenants]

    def _rng(self, *parts: object) -> random.Random:
        return random.Random(":".join(str(p) for p in (self.seed, *parts)))

    def _log(self, tenant_id: str, component: str, level: str, message: str) -> LogLine:
        line = LogLine(
            f"log-{len(self.logs) + 1:05d}", self.minute, tenant_id, component, level, message
        )
        self.logs.append(line)
        return line

    def _enqueue(self, tenant_id: str, payload: str, *, poison: bool) -> Message:
        self._msg_seq += 1
        msg = Message(f"msg-{tenant_id}-{self._msg_seq:04d}", payload, poison)
        self.state[tenant_id].queue.append(msg)
        return msg

    # ---- faults -------------------------------------------------------------------------------

    def inject(self, kind: str, tenant_id: str, *, payload: str | None = None) -> dict:
        """Start a fault now. Returns ground truth for evals (never shown to the agent)."""
        st = self.state[tenant_id]
        if kind == "db_latency":
            st.pool_demand = 1.6  # demand exceeds the pool: queries queue for connections
            return {"kind": kind, "pool_size": st.pool_size}
        if kind == "queue_backlog":
            msg = self._enqueue(
                tenant_id, payload or "invoice batch 2291: totals mismatch", poison=True
            )
            return {"kind": kind, "message_id": msg.id}
        if kind == "bad_metadata_change":
            history = self._overrides[(tenant_id, "worker")]
            current = history[-1]
            broken = ObjectOverride(
                add_fields=current.add_fields,
                hide_fields=sorted(set(current.hide_fields) | {"manager"}),
                relabel=current.relabel,
            )
            self.store.put_override(st.tenant.context(), "worker", broken)
            history.append(broken)
            version = len(history)
            change = MetadataChange(
                f"change-{len(self.changes) + 1:03d}",
                tenant_id,
                "worker",
                version,
                self.minute,
                "worker: hide field 'manager'",
            )
            self.changes.append(change)
            st.metadata_broken = True
            return {
                "kind": kind,
                "object": "worker",
                "to_version": version - 1,
                "change_id": change.id,
            }
        raise ValueError(f"unknown fault {kind!r}; known: {FAULT_TYPES}")

    # ---- remediation effects (called only by approved remediations) ---------------------------

    def set_pool_size(self, tenant_id: str, size: int) -> None:
        st = self.state[tenant_id]
        st.pool_demand = st.pool_demand * st.pool_size / size
        st.pool_size = size

    def set_workers(self, tenant_id: str, replicas: int) -> None:
        self.state[tenant_id].workers = replicas

    def dead_letter(self, tenant_id: str, message_id: str) -> bool:
        st = self.state[tenant_id]
        for msg in st.queue:
            if msg.id == message_id:
                st.queue.remove(msg)
                st.dead_letter.append(msg)
                return True
        return False

    def revert_metadata(self, tenant_id: str, obj: str, to_version: int) -> bool:
        history = self._overrides.get((tenant_id, obj))
        if not history or not 1 <= to_version < len(history):
            return False
        target = history[to_version - 1]
        self.store.put_override(self.state[tenant_id].tenant.context(), obj, target)
        history.append(target)
        self.changes.append(
            MetadataChange(
                f"change-{len(self.changes) + 1:03d}",
                tenant_id,
                obj,
                len(history),
                self.minute,
                f"{obj}: revert to version {to_version}",
            )
        )
        self.state[tenant_id].metadata_broken = "manager" in target.hide_fields
        return True

    # ---- simulation ---------------------------------------------------------------------------

    def run(self, minutes: int) -> None:
        for _ in range(minutes):
            self.minute += 1
            for tid in self.tenant_ids():
                self._tick(tid)

    def _tick(self, tid: str) -> None:
        st = self.state[tid]
        rng = self._rng(tid, self.minute)

        # Database: latency rises sharply once demand exceeds the pool.
        demand = st.pool_demand * rng.uniform(0.9, 1.1)
        in_use = min(st.pool_size, round(st.pool_size * demand))
        base = rng.uniform(0.3, 0.6) * st.latency_threshold_ms
        p95 = (
            base
            if demand < 0.95
            else st.latency_threshold_ms * (1.5 + demand) * rng.uniform(1, 1.2)
        )
        st.metrics["store.latency_p95_ms"].append(round(p95, 1))
        st.metrics["store.pool_in_use"].append(float(in_use))
        if demand >= 0.95 and rng.random() < 0.7:
            self._log(
                tid,
                "store",
                "WARN",
                f"query waited {p95 / 1000:.2f}s for a pool connection "
                f"(pool {in_use}/{st.pool_size} in use)",
            )

        # Queue: healthy workers keep the backlog near zero. A poison message is retried forever and
        # stalls the workers, so the backlog climbs past the threshold within ~8 minutes; once it is
        # dead-lettered, the backlog drains over a few minutes.
        poison = [m for m in st.queue if m.poison]
        capacity = st.workers * rng.randint(4, 6)
        step = st.backlog_threshold // 8
        if poison:
            st.backlog += step + rng.randint(0, step // 10)
        else:
            st.backlog = max(0, st.backlog - 2 * step) + rng.randint(0, 3) * st.workers
        for msg in poison:
            msg.attempts += 1
            self._log(
                tid,
                "queue",
                "ERROR",
                f"job {msg.id} failed (attempt {msg.attempts}): could not process payload "
                f"{msg.payload[:300]!r}",
            )
        st.metrics["queue.backlog"].append(float(st.backlog))
        if rng.random() < 0.3:
            self._log(tid, "queue", "INFO", f"processed {capacity} jobs; backlog {st.backlog}")

        # Requests: a hidden required-by-workflow field breaks leave approvals.
        error_rate = rng.uniform(0.0, 0.01) + (
            0.2 + rng.uniform(0, 0.05) if st.metadata_broken else 0
        )
        st.metrics["requests.error_rate"].append(round(error_rate, 4))
        if st.metadata_broken and rng.random() < 0.8:
            self._log(
                tid,
                "api",
                "ERROR",
                "leave_request approval failed: worker.manager is not visible for this tenant",
            )
        elif rng.random() < 0.2:
            self._log(tid, "api", "INFO", f"served {rng.randint(80, 120)} requests")

    # ---- views (unfiltered; callers filter by tenant) -----------------------------------------

    def window(self, tid: str, metric: str, minutes: int) -> list[float]:
        return self.state[tid].metrics[metric][-minutes:]

    def alerts(self, tid: str) -> list[Alert]:
        st = self.state[tid]
        checks = (
            ("store.latency_p95_ms", st.latency_threshold_ms),
            ("queue.backlog", st.backlog_threshold),
            ("requests.error_rate", ERROR_RATE_THRESHOLD),
        )
        out = []
        for metric, threshold in checks:
            recent = self.window(tid, metric, ALERT_WINDOW)
            if recent and sum(recent) / len(recent) > threshold:
                value = round(sum(recent) / len(recent), 4)
                out.append(Alert(f"alert-{tid}-{metric}", tid, metric, value, threshold))
        return out
