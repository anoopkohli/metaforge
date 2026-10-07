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
