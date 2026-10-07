"""Tenant context.

Invariant: the tenant for any operation comes from a `TenantContext` built from an authenticated
token. There is no default tenant. Code that needs a tenant and has none must fail loudly.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

# Conservative on purpose: tenant IDs end up in filters, index names and log lines.
_TENANT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")


class MissingTenantContext(RuntimeError):
    """Raised when tenant-scoped code runs without a TenantContext."""


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: str
    subject: str = "anonymous"
    roles: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not _TENANT_ID_RE.match(self.tenant_id):
            raise ValueError(f"invalid tenant_id: {self.tenant_id!r}")


_current: ContextVar[TenantContext | None] = ContextVar("metaforge_tenant", default=None)


def current_tenant() -> TenantContext | None:
    """Return the active context, or None. Prefer `require_tenant()` in tenant-scoped code."""
    return _current.get()


def require_tenant() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise MissingTenantContext("no TenantContext is active; refusing to run unscoped")
    return ctx


@contextmanager
def tenant_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    """Activate `ctx` for the duration of the block. Restores the previous value on exit."""
    if not isinstance(ctx, TenantContext):
        raise TypeError("tenant_scope requires a TenantContext")
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
