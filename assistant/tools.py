"""The assistant's tools, scoped to the signed-in person, with approvals and an audit trail.

Invariants:
- Identity (tenant, person, roles) comes only from the `TenantContext`. Tool arguments are strict
  models with no identity fields; anything extra is rejected.
- Reads go through the same code as the rest of the app: documents through `RagService` (tenant
  filtered), data through `dataqa` (row scopes and restricted fields enforced).
- Writes are proposals only. A record update is checked against the effective schema, restricted
  fields, the caller's row scope and reference targets, when proposed and again when approved.
  Schema changes need a manager. `approve` is called by the UI on the user's click, never by the agent.
- Every call, denial, proposal, approval and rejection is appended to the tenant's audit log, with
  names, counts and outcomes only, never record values.

Naive mode (exact env value only) trusts identity arguments, skips scopes and restrictions, lets anyone
propose schema changes and executes every proposal at once.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.audit import AuditLog
from core.store.models import IDENT
from core.store.sqlite_store import MetadataStore
from core.tenancy.context import MissingTenantContext, TenantContext
from dataqa import MANAGER_ROLE, QueryError, describe, execute, parse_query, schema_for
from dataqa.query import _check_value, visible_records
from mcp_server.tools import Confirmation, TenantTools, ToolError
from rag.service import RagError, RagService

log = logging.getLogger("metaforge.assistant")

NAIVE_ENV = "METAFORGE_UNSAFE_NAIVE_ASSISTANT"
NAIVE_VALUE = "trust-the-assistant"
MAX_STEPS = 8
NAIVE_MAX_STEPS = 200
MAX_ROWS = 50
IDENTITY_KEYS = ("tenant_id", "tenant", "as_user", "user_id", "role", "roles")


def naive_assistant_enabled() -> bool:
    return os.environ.get(NAIVE_ENV) == NAIVE_VALUE


class AssistantError(Exception):
    """A tool call was rejected. The message is safe to show the model and the user."""


@dataclass
class Proposal:
    id: str
    tenant_id: str
    kind: str  # record_update | schema_change
    summary: str
    payload: dict[str, Any]
    proposed_by: str
    status: str = "proposed"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "payload": self.payload,
            "proposed_by": self.proposed_by,
            "status": self.status,
        }


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchArgs(_Args):
    query: str = Field(min_length=1, max_length=300)


class NoArgs(_Args):
    pass


class DescribeArgs(_Args):
    name: str = Field(pattern=IDENT)


class QueryArgs(_Args):
    query: dict[str, Any]


class UpdateArgs(_Args):
    object: str = Field(pattern=IDENT)
    record_id: str = Field(min_length=1, max_length=64)
    changes: dict[str, Any] = Field(min_length=1, max_length=5)


class SchemaArgs(_Args):
    proposal: dict[str, Any]


TOOL_ARGS: dict[str, type[_Args]] = {
    "search_documents": SearchArgs,
    "list_data": NoArgs,
    "describe_object": DescribeArgs,
    "query_data": QueryArgs,
    "propose_record_update": UpdateArgs,
    "propose_schema_change": SchemaArgs,
}


def _errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or '<args>'}: {e['msg']}"
        for e in exc.errors(include_url=False)[:4]
    )


@dataclass
class AssistantTools:
    store: MetadataStore
    rag: RagService | None
    audit: AuditLog | None
    naive: bool = False
    _proposals: dict[tuple[str, str], Proposal] = field(default_factory=dict)
    _schema: TenantTools | None = None

    def __post_init__(self) -> None:
        if self.naive and not naive_assistant_enabled():
            raise RuntimeError(f"naive assistant needs {NAIVE_ENV}={NAIVE_VALUE}")
        if self.naive:
            log.warning("NAIVE ASSISTANT MODE: identity args trusted, checks off. Never deploy.")
        self._schema = TenantTools(self.store) if self.store is not None else None

    # ---- audit --------------------------------------------------------------------------------

    def record(self, ctx: TenantContext, action: str, **detail: Any) -> None:
        if self.audit is not None:
            self.audit.append(ctx, action, {k: v for k, v in detail.items() if v is not None})

    # ---- dispatch -----------------------------------------------------------------------------

    def call(self, ctx: TenantContext | None, name: str, args: dict | None) -> dict:
        if not isinstance(ctx, TenantContext):
            raise MissingTenantContext("assistant tool call without a TenantContext")
        raw = dict(args or {})
        if self.naive:  # the deliberate bug: identity from the model
            tenant = raw.pop("tenant_id", None) or raw.pop("tenant", None)
            user = raw.pop("as_user", None) or raw.pop("user_id", None)
            role = raw.pop("role", None) or raw.pop("roles", None)
            if tenant or user or role:
                ctx = TenantContext(
                    tenant_id=str(tenant or ctx.tenant_id),
                    subject=str(user or ctx.subject),
                    roles=frozenset({MANAGER_ROLE}) if role else ctx.roles,
                )
        try:
            model = TOOL_ARGS.get(name)
            if model is None:
                raise AssistantError(
                    f"unknown tool {name!r}; available: {', '.join(TOOL_ARGS)}, finish"
                )
            try:
                parsed = model.model_validate(raw)
            except ValidationError as exc:
                raise AssistantError(f"invalid arguments: {_errors(exc)}") from exc
            result = getattr(self, f"_{name}")(ctx, parsed)
        except AssistantError as exc:
            outcome = "denied" if "restricted" in str(exc) or "manager" in str(exc) else "error"
            self.record(ctx, "assistant.tool", tool=name, outcome=outcome, error=str(exc)[:200])
            raise
        self.record(
            ctx,
            "assistant.tool",
            tool=name,
            outcome="ok",
            object=result.get("object") if isinstance(result.get("object"), str) else None,
            rows=result.get("matched") if isinstance(result.get("matched"), int) else None,
        )
        return result

    # ---- reads --------------------------------------------------------------------------------

    def _search_documents(self, ctx: TenantContext, args: SearchArgs) -> dict:
        if self.rag is None:
            raise AssistantError("no document index is configured")
        try:
            hits = self.rag.retrieve(ctx, args.query, k=4)
        except RagError as exc:
            raise AssistantError(str(exc)) from exc
        return {
            "results": [
                {"source": h.chunk.chunk_id, "title": h.chunk.title, "text": h.chunk.text}
                for h in hits
            ]
        }

    def _list_data(self, ctx: TenantContext, args: NoArgs) -> dict:
        return {"objects": schema_for(self.store, ctx)}

    def _describe_object(self, ctx: TenantContext, args: DescribeArgs) -> dict:
        obj = next((o for o in schema_for(self.store, ctx) if o["name"] == args.name), None)
        if obj is None:
            raise AssistantError(f"no object named {args.name!r}")
        return {"object": obj["name"], **obj}

    def _query_data(self, ctx: TenantContext, args: QueryArgs) -> dict:
        try:
            query = parse_query(args.query)
            result = execute(self.store, ctx, query, naive=self.naive)
        except QueryError as exc:
            raise AssistantError(str(exc)) from exc
        return {
            "object": query.object,
            "description": describe(query),
            "columns": result.columns,
            "rows": result.rows[:MAX_ROWS],
            "matched": result.matched,
            "truncated": result.truncated or len(result.rows) > MAX_ROWS,
        }

    # ---- proposals ----------------------------------------------------------------------------

    def _check_update(self, ctx: TenantContext, obj: str, record_id: str, changes: dict) -> dict:
        view = self.store.get_effective_schema(ctx, obj)
        if view is None:
            raise AssistantError(f"no object named {obj!r}")
        rows = visible_records(self.store, ctx, obj, naive=self.naive)
        record = next((r for r in rows if r.get("id") == record_id), None)
        if record is None:  # same message whether it is out of scope or does not exist
            raise AssistantError(f"no {obj} record {record_id!r} that you can see")
        fields = {f.name: f for f in view.fields}
        manager = MANAGER_ROLE in ctx.roles or self.naive
        for name, value in changes.items():
            f = fields.get(name)
            if name == "id" or f is None:
                raise AssistantError(f"{obj!r} has no updatable field {name!r}")
            if f.restricted and not manager:
                raise AssistantError(f"field {name!r} is restricted to managers")
            if problem := _check_value(f, "=", value):
                raise AssistantError(problem)
            if f.type == "reference" and f.reference_to:
                targets = {r["id"] for r in self.store.list_records(ctx, f.reference_to)}
                if value not in targets:
                    raise AssistantError(f"{name!r} must be the id of an existing {f.reference_to}")
        return record

    def _new_id(self) -> str:
        return f"prop-{len(self._proposals) + 1}"

    def _register(self, ctx: TenantContext, proposal: Proposal) -> dict:
        self._proposals[(ctx.tenant_id, proposal.id)] = proposal
        self.record(
            ctx,
            "proposal.created",
            proposal_id=proposal.id,
            kind=proposal.kind,
            object=proposal.payload.get("object"),
            record_id=proposal.payload.get("record_id"),
            fields=sorted(proposal.payload.get("changes", {})),
        )
        if self.naive:  # the vulnerable pattern: the agent's word is the change
            self._execute(ctx, proposal)
        return {
            "proposal_id": proposal.id,
            "summary": proposal.summary,
            "status": proposal.status,
            "note": "waiting for the user's approval; nothing has changed yet"
            if proposal.status == "proposed"
            else "executed",
        }

    def _propose_record_update(self, ctx: TenantContext, args: UpdateArgs) -> dict:
        self._check_update(ctx, args.object, args.record_id, args.changes)
        summary = f"Update {args.object} {args.record_id}: " + ", ".join(
            f"{k} -> {v!r}" for k, v in args.changes.items()
        )
        payload = {"object": args.object, "record_id": args.record_id, "changes": args.changes}
        return self._register(
            ctx,
            Proposal(self._new_id(), ctx.tenant_id, "record_update", summary, payload, ctx.subject),
        )

    def _propose_schema_change(self, ctx: TenantContext, args: SchemaArgs) -> dict:
        if MANAGER_ROLE not in ctx.roles and not self.naive:
            raise AssistantError("schema changes can only be proposed by a manager")
        try:
            res = self._schema.call(ctx, "propose_change", {"proposal": args.proposal})
        except ToolError as exc:
            raise AssistantError(str(exc)) from exc
        if not res.get("accepted"):
            raise AssistantError("; ".join(res.get("errors", ["invalid change"])))
        payload = {"change_id": res["change_id"], "object": args.proposal.get("target")
                   or args.proposal.get("object", {}).get("name")}  # fmt: skip
        return self._register(
            ctx,
            Proposal(
                self._new_id(), ctx.tenant_id, "schema_change", res["summary"], payload, ctx.subject
            ),
        )

    def _execute(self, ctx: TenantContext, proposal: Proposal) -> None:
        p = proposal.payload
        if proposal.kind == "record_update":
            records = self.store.list_records(ctx, p["object"])
            for r in records:
                if r["id"] == p["record_id"]:
                    r.update(p["changes"])
            self.store.put_records(ctx, p["object"], records)
        else:
            cid = p["change_id"]
            try:
                self._schema.call(
                    ctx, "apply_change", {"change_id": cid}, confirmation=Confirmation(cid)
                )
            except ToolError as exc:
                raise AssistantError(str(exc)) from exc
        proposal.status = "executed"
        self.record(ctx, "proposal.executed", proposal_id=proposal.id, kind=proposal.kind)

    # ---- the user's side: approve / reject (never called by the agent) ------------------------

    def _pending(self, ctx: TenantContext, proposal_id: str) -> Proposal:
        if not isinstance(ctx, TenantContext):
            raise MissingTenantContext("approval without a TenantContext")
        proposal = self._proposals.get((ctx.tenant_id, proposal_id))
        if proposal is None or proposal.status != "proposed":
            raise AssistantError(f"no pending proposal {proposal_id!r}")
        return proposal

    def approve(self, ctx: TenantContext, proposal_id: str) -> dict:
        proposal = self._pending(ctx, proposal_id)
        if proposal.kind == "record_update":  # state or the approver's rights may differ now
            p = proposal.payload
            self._check_update(ctx, p["object"], p["record_id"], p["changes"])
        elif MANAGER_ROLE not in ctx.roles:
            raise AssistantError("schema changes can only be approved by a manager")
        self.record(ctx, "proposal.approved", proposal_id=proposal_id, kind=proposal.kind)
        self._execute(ctx, proposal)
        return proposal.to_dict()

    def reject(self, ctx: TenantContext, proposal_id: str) -> dict:
        proposal = self._pending(ctx, proposal_id)
        proposal.status = "rejected"
        self.record(ctx, "proposal.rejected", proposal_id=proposal_id, kind=proposal.kind)
        return proposal.to_dict()

    def proposals(self, ctx: TenantContext) -> list[dict]:
        return [p.to_dict() for (tid, _), p in self._proposals.items() if tid == ctx.tenant_id]

    def all_proposals(self) -> list[Proposal]:
        return list(self._proposals.values())
