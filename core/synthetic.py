"""Deterministic synthetic tenants and metadata.

Everything is generated from a seed, so tests and evals are reproducible. Each tenant gets a
unique canary string embedded in its own data; later stages (RAG, MCP, triage) use canaries to
detect cross-tenant leakage objectively: if tenant A ever sees tenant B's canary, that is a leak.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from core.store.models import FieldDef, ObjectDef, ObjectOverride
from core.store.sqlite_store import MetadataStore
from core.tenancy.context import TenantContext

_TENANT_NAMES = [
    "acme", "borealis", "cinder", "delta", "ember", "fjord", "gable", "harbor", "ivory", "juniper",
]  # fmt: skip

_EXTRA_FIELDS = [
    FieldDef(name="cost_center", type="string", label="Cost Center"),
    FieldDef(name="region", type="string"),
    FieldDef(name="priority", type="integer"),
    FieldDef(name="is_confidential", type="boolean"),
    FieldDef(name="effective_date", type="date"),
]


@dataclass(frozen=True)
class SyntheticTenant:
    tenant_id: str
    canary: str

    def context(self, subject: str = "demo-user") -> TenantContext:
        return TenantContext(tenant_id=self.tenant_id, subject=subject)


def base_catalog() -> list[ObjectDef]:
    """Platform-level objects every tenant can see (and override)."""
    return [
        ObjectDef(
            name="worker",
            label="Worker",
            fields=[
                FieldDef(name="full_name", type="string", required=True),
                FieldDef(name="hire_date", type="date", required=True),
                FieldDef(name="manager", type="reference", reference_to="worker"),
                FieldDef(name="notes", type="string"),
            ],
        ),
        ObjectDef(
            name="leave_request",
            label="Leave Request",
            fields=[
                FieldDef(name="worker", type="reference", reference_to="worker", required=True),
                FieldDef(name="start_date", type="date", required=True),
                FieldDef(name="end_date", type="date", required=True),
                FieldDef(name="reason", type="string"),
            ],
        ),
        ObjectDef(
            name="expense_report",
            label="Expense Report",
            fields=[
                FieldDef(name="submitter", type="reference", reference_to="worker", required=True),
                FieldDef(name="total", type="decimal", required=True),
                FieldDef(name="memo", type="string"),
            ],
        ),
    ]


def seed_demo(store: MetadataStore, n_tenants: int = 5, seed: int = 7) -> list[SyntheticTenant]:
    """Populate the store with the base catalog plus n tenants that each customise it."""
    if not 1 <= n_tenants <= len(_TENANT_NAMES):
        raise ValueError(f"n_tenants must be between 1 and {len(_TENANT_NAMES)}")
    rng = random.Random(seed)

    for obj in base_catalog():
        store.platform_put_base_object(obj)

    names = rng.sample(_TENANT_NAMES, n_tenants)
    tenants: list[SyntheticTenant] = []
    for name in names:
        canary = f"CANARY-{name}-{rng.getrandbits(32):08x}"
        tenant = SyntheticTenant(tenant_id=name, canary=canary)
        ctx = tenant.context()

        # Every tenant: hide the free-text notes field and add 1-2 extra fields to worker.
        extras = rng.sample(_EXTRA_FIELDS, rng.randint(1, 2))
        store.put_override(
            ctx,
            "worker",
            ObjectOverride(
                add_fields=extras,
                hide_fields=["notes"],
                relabel={"full_name": f"{name.title()} Employee Name"},
            ),
        )

        # Every tenant: one custom object whose label carries the tenant's canary.
        store.put_object(
            ctx,
            ObjectDef(
                name=f"{name}_asset",
                label=f"{name.title()} Asset ({canary})",
                fields=[
                    FieldDef(name="tag", type="string", required=True),
                    FieldDef(name="assigned_to", type="reference", reference_to="worker"),
                ],
            ),
        )
        tenants.append(tenant)
    return tenants


# ---- stage 3: synthetic tenant documents ------------------------------------------------------


@dataclass(frozen=True)
class SyntheticDocument:
    doc_id: str
    title: str
    text: str
    facts: dict[str, str]  # fact name -> exact string an answer must contain


_TEAMS = ["facilities", "service", "operations", "workplace", "support"]
_ROTATIONS = ["platform", "database", "infrastructure", "core-services"]


def synthetic_documents(tenant: SyntheticTenant, seed: int = 7) -> list[SyntheticDocument]:
    """Six policy and runbook documents with tenant-specific facts and the tenant's canary.

    Every paragraph carries the canary, so any chunk that crosses a tenant boundary is detectable.
    Facts are drawn from a per-tenant RNG, so two tenants' answers to the same question differ.
    """
    rng = random.Random(f"{seed}:{tenant.tenant_id}")
    ref = f"(ref {tenant.canary})"
    name = tenant.tenant_id.title()

    limit = rng.choice([500, 750, 1000, 1500, 2500])
    receipt = rng.choice([25, 50, 75])
    carry = rng.randint(3, 15)
    notice = rng.choice([5, 10, 14, 20])
    hours = rng.choice([4, 12, 24, 48])
    team = rng.choice(_TEAMS)
    p95 = rng.choice([150, 250, 400, 800])
    pool = rng.choice([20, 40, 60, 100])
    rotation = rng.choice(_ROTATIONS)
    backlog = rng.choice([1000, 5000, 10000, 50000])
    workers = rng.choice([4, 8, 12, 16])
    laptop = rng.randint(2, 7)
    buddy = rng.choice([2, 4, 6, 8])

    return [
        SyntheticDocument(
            "expense_policy",
            f"{name} expense policy",
            f"Expense reports above {limit} dollars need director approval before payment. "
            f"{ref}\n\n"
            f"Receipts are required for any single item over {receipt} dollars. Reports are "
            f"submitted through the expense report object and paid monthly. {ref}",
            {"expense_limit": str(limit), "receipt_threshold": str(receipt)},
        ),
        SyntheticDocument(
            "leave_policy",
            f"{name} leave policy",
            f"Workers may carry over up to {carry} unused leave days into the next year. {ref}\n\n"
            f"Planned leave longer than one week needs {notice} days notice, recorded as a leave "
            f"request with start and end dates. {ref}",
            {"carryover_days": str(carry), "notice_days": str(notice)},
        ),
        SyntheticDocument(
            "asset_handling",
            f"{name} asset handling",
            f"Lost or stolen assets must be reported within {hours} hours to the {team} desk. {ref}"
            f"\n\nEvery asset has a tag and may be assigned to one worker at a time. {ref}",
            {"report_hours": str(hours), "asset_team": team},
        ),
        SyntheticDocument(
            "runbook_db_latency",
            f"{name} runbook: database latency",
            f"If p95 query latency exceeds {p95} ms, first check connection pool saturation. The "
            f"pool size is {pool} connections. {ref}\n\n"
            f"If the pool is not saturated, look for long-running queries and lock waits. Escalate "
            f"to the {rotation} on-call rotation after 15 minutes. {ref}",
            {"latency_threshold_ms": str(p95), "pool_size": str(pool), "rotation": rotation},
        ),
        SyntheticDocument(
            "runbook_queue_backlog",
            f"{name} runbook: queue backlog",
            f"When the job queue backlog passes {backlog} messages, scale the workers to {workers} "
            f"replicas. {ref}\n\n"
            f"If the backlog keeps growing after scaling, check for a poison message that fails "
            f"and retries forever, and move it to the dead-letter queue. {ref}",
            {"backlog_threshold": str(backlog), "worker_replicas": str(workers)},
        ),
        SyntheticDocument(
            "onboarding",
            f"{name} onboarding guide",
            f"New workers receive a laptop within {laptop} days of their hire date. {ref}\n\n"
            f"Each new worker joins a buddy program that lasts {buddy} weeks. {ref}",
            {"laptop_days": str(laptop), "buddy_weeks": str(buddy)},
        ),
    ]
