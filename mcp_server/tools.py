"""Tenant-scoped tools over the metadata store. No MCP SDK dependency; `server.py` adapts these.

Invariants:
- The tenant is the `TenantContext` passed to `call()`, which the transport builds from a verified
  token. Tenant-like arguments from the model are stripped, recorded in the audit log, and ignored.
- Arguments are validated strictly; anything unexpected is an error.
- Only `apply_change` writes, and only with a `Confirmation` for that exact change. A `Confirmation`
  is created by the transport after a human approved; the model cannot produce one.
- Pending changes are keyed by (tenant, random ID), single-use, and expire.

Naive mode is a deliberate vulnerability for the attack suite: it trusts a model-supplied tenant ID.
It needs an exact environment value, and logs a warning on every call.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.store.models import IDENT, ObjectDef
from core.store.sqlite_store import MetadataStore
from core.tenancy.context import MissingTenantContext, TenantContext
from generator.generate import ProposalRejected, apply_proposal
from generator.proposal import CreateObject, OverrideObject, ProposalError, Refuse, parse_proposal
from generator.validate import check_proposal

log = logging.getLogger("metaforge.mcp")

TENANT_ARG_KEYS = frozenset({"tenant_id", "tenant", "tid", "tenantId"})
NAIVE_ENV = "METAFORGE_UNSAFE_NAIVE_MCP"
NAIVE_VALUE = "trust-model-tenant-arg"


def naive_mode_from_env() -> bool:
    return os.environ.get(NAIVE_ENV) == NAIVE_VALUE


class ToolError(Exception):
    """A tool call failed. The message is safe to return to the model."""


@dataclass(frozen=True)
class Confirmation:
    """Proof, created by the transport, that a human approved this change ID."""

    change_id: str


@dataclass(frozen=True)
class AuditEvent:
    tenant_id: str
    tool: str
    ignored_args: tuple[str, ...]
    outcome: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    read_only: bool
    destructive: bool

    @property
    def input_schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()


@dataclass
class _Pending:
    proposal: CreateObject | OverrideObject
    summary: str
    expires_at: float


# ---- argument models --------------------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListObjectsArgs(_Args):
    pass


class DescribeObjectArgs(_Args):
    name: str = Field(pattern=IDENT, description="Object name, as returned by list_objects.")


class ProposeChangeArgs(_Args):
    proposal: dict[str, Any] = Field(
        description=(
            'A proposal object: {"action": "create_object", "object": {...}} or '
            '{"action": "override_object", "target": "<platform object>", "override": {...}}.'
        )
    )


class ApplyChangeArgs(_Args):
    change_id: str = Field(min_length=1, max_length=64)


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "list_objects",
        "List the objects this tenant can see: platform objects and its own custom objects.",
        ListObjectsArgs,
        read_only=True,
        destructive=False,
    ),
    ToolSpec(
        "describe_object",
        "Show the fields of one object as this tenant sees it (its overrides applied).",
        DescribeObjectArgs,
        read_only=True,
        destructive=False,
    ),
    ToolSpec(
        "propose_change",
        "Validate a metadata change and park it for approval. Writes nothing. Returns a change_id "
        "and a summary, or the validation errors to fix.",
        ProposeChangeArgs,
        read_only=False,
        destructive=False,
    ),
    ToolSpec(
        "apply_change",
        "Apply a parked change. The user is asked to approve it first; without approval nothing "
        "is written.",
        ApplyChangeArgs,
        read_only=False,
        destructive=True,
    ),
)


def _field_dict(f) -> dict[str, Any]:
    return {
        "name": f.name,
        "type": f.type,
        "required": f.required,
        "label": f.label,
        "reference_to": f.reference_to,
        "restricted": f.restricted,
    }


def summarise(proposal: CreateObject | OverrideObject) -> str:
    if isinstance(proposal, CreateObject):
        obj = proposal.object
        fields = ", ".join(f"{f.name} ({f.type})" for f in obj.fields)
        return f"Create object '{obj.name}' with fields: {fields}"
    o = proposal.override
    parts = []
    if o.add_fields:
        parts.append("add " + ", ".join(f"{f.name} ({f.type})" for f in o.add_fields))
    if o.hide_fields:
        parts.append("hide " + ", ".join(o.hide_fields))
    if o.relabel:
        parts.append("relabel " + ", ".join(f"{k} -> '{v}'" for k, v in o.relabel.items()))
    return f"Change '{proposal.target}' for this tenant: " + ("; ".join(parts) or "no changes")


@dataclass
class TenantTools:
    store: MetadataStore
    naive: bool = False
    ttl_s: float = 600.0
    max_pending: int = 20
    clock: Callable[[], float] = time.time
    audit: list[AuditEvent] = field(default_factory=list)
    _pending: dict[tuple[str, str], _Pending] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.naive and not naive_mode_from_env():
            raise RuntimeError(f"naive mode needs {NAIVE_ENV}={NAIVE_VALUE}; refusing to start")
        if self.naive:
            log.warning("NAIVE MCP MODE: model-supplied tenant IDs are trusted. Never deploy this.")

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return SPECS

    # ---- dispatch -----------------------------------------------------------------------------

    def call(
        self,
        ctx: TenantContext | None,
        name: str,
        args: dict[str, Any] | None,
        *,
        confirmation: Confirmation | None = None,
    ) -> dict[str, Any]:
        if not isinstance(ctx, TenantContext):
            raise MissingTenantContext("tool call without a TenantContext")
        spec = next((s for s in SPECS if s.name == name), None)
        if spec is None:
            raise ToolError(f"unknown tool {name!r}")

        raw = dict(args or {})
        ignored = tuple(sorted(k for k in raw if k in TENANT_ARG_KEYS))
        supplied = {k: raw.pop(k) for k in ignored}
        if self.naive:
            log.warning("NAIVE MCP MODE: honouring tenant args %s", supplied)
            for value in supplied.values():
                ctx = TenantContext(tenant_id=str(value), subject=ctx.subject)

        try:
            parsed = spec.args_model.model_validate(raw)
        except ValidationError as exc:
            self._record(ctx, name, ignored, "invalid-args")
            msgs = [f"{'.'.join(map(str, e['loc'])) or '<args>'}: {e['msg']}" for e in exc.errors()]
            raise ToolError("invalid arguments: " + "; ".join(msgs[:5])) from exc

        try:
            result = getattr(self, f"_{name}")(ctx, parsed, confirmation)
        except ToolError:
            self._record(ctx, name, ignored, "error")
            raise
        self._record(ctx, name, ignored, "ok")
        return result

    def _record(
        self, ctx: TenantContext, tool: str, ignored: tuple[str, ...], outcome: str
    ) -> None:
        self.audit.append(AuditEvent(ctx.tenant_id, tool, ignored, outcome))
        if ignored:
            log.warning("tenant=%s tool=%s ignored model-supplied %s", ctx.tenant_id, tool, ignored)

    # ---- tools --------------------------------------------------------------------------------

    def _list_objects(self, ctx, args: ListObjectsArgs, _c) -> dict[str, Any]:
        objects = []
        for name in self.store.list_objects(ctx):
            schema = self.store.get_effective_schema(ctx, name)
            if schema is None:
                continue
            kind = "platform" if self.store.get_base_object(name) is not None else "custom"
            objects.append({"name": name, "kind": kind, "label": schema.label})
        return {"objects": objects}

    def _describe_object(self, ctx, args: DescribeObjectArgs, _c) -> dict[str, Any]:
        schema: ObjectDef | None = self.store.get_effective_schema(ctx, args.name)
        if schema is None:
            raise ToolError(f"no object named {args.name!r}")
        kind = "platform" if self.store.get_base_object(args.name) is not None else "custom"
        return {
            "name": schema.name,
            "kind": kind,
            "label": schema.label,
            "fields": [_field_dict(f) for f in schema.fields],
        }

    def _propose_change(self, ctx, args: ProposeChangeArgs, _c) -> dict[str, Any]:
        try:
            proposal = parse_proposal(json.dumps(args.proposal))
        except ProposalError as exc:
            return {"accepted": False, "errors": [str(exc)]}
        if isinstance(proposal, Refuse):
            return {"accepted": False, "errors": ["a refusal is not a change; nothing to propose"]}
        errors = check_proposal(self.store, ctx, proposal)
        if errors:
            return {"accepted": False, "errors": errors}

        self._expire()
        mine = sum(1 for (tid, _) in self._pending if tid == ctx.tenant_id)
        if mine >= self.max_pending:
            raise ToolError(f"too many pending changes (max {self.max_pending}); apply or wait")
        change_id = secrets.token_urlsafe(16)
        summary = summarise(proposal)
        self._pending[(ctx.tenant_id, change_id)] = _Pending(
            proposal, summary, self.clock() + self.ttl_s
        )
        return {
            "accepted": True,
            "change_id": change_id,
            "summary": summary,
            "expires_in_s": int(self.ttl_s),
        }

    def _apply_change(self, ctx, args: ApplyChangeArgs, confirmation) -> dict[str, Any]:
        self._expire()
        key = (ctx.tenant_id, args.change_id)
        if key not in self._pending:
            raise ToolError(f"no pending change {args.change_id!r}")
        if not isinstance(confirmation, Confirmation) or confirmation.change_id != args.change_id:
            raise ToolError("this change needs the user's confirmation; nothing was written")
        pending = self._pending.pop(key)  # single use, even if the write below fails
        try:
            version = apply_proposal(self.store, ctx, pending.proposal)
        except ProposalRejected as exc:
            raise ToolError(f"change no longer valid: {exc}") from exc
        return {"applied": True, "summary": pending.summary, "version": version}

    # ---- helpers for the transport ------------------------------------------------------------

    def pending_summary(self, ctx: TenantContext, change_id: str) -> str | None:
        """Summary to show the human, or None if this tenant has no such live change."""
        self._expire()
        pending = self._pending.get((ctx.tenant_id, change_id))
        return pending.summary if pending else None

    def _expire(self) -> None:
        now = self.clock()
        for key in [k for k, p in self._pending.items() if p.expires_at <= now]:
            del self._pending[key]
