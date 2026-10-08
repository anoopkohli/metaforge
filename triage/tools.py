"""Tenant-scoped triage tools over a `ChaosEnvironment`, plus remediation proposals and approval.

Invariants:
- Every read filters on the caller's `TenantContext`. Tenant-like arguments from the model are
  stripped and audited.
- `propose_remediation` only records a proposal, and only if the action is allow-listed, its
  parameters are within bounds, and its target belongs to the caller's tenant. Validation never
  looks at why the model asked.
- Nothing executes without `approve(ctx, proposal_id, Confirmation(proposal_id))`, which is called
  by a human-facing surface outside the agent loop. Proposals are single use and re-validated on
  approval.

Naive mode (exact env value only) trusts tenant arguments, skips validation, executes proposals as
soon as they are made, looks targets up across tenants, and lifts the step cap. It exists so the
attack suite can show each control matters.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from chaos.env import METRICS, ChaosEnvironment
from core.store.models import IDENT
from core.tenancy.context import MissingTenantContext, TenantContext
from mcp_server.tools import TENANT_ARG_KEYS, Confirmation
from rag.service import RagError, RagService

log = logging.getLogger("metaforge.triage")

NAIVE_ENV = "METAFORGE_UNSAFE_NAIVE_TRIAGE"
NAIVE_VALUE = "trust-the-agent"
MAX_STEPS = 10
NAIVE_MAX_STEPS = 200
MAX_WORKERS = 16
MAX_POOL = 200
MAX_PROPOSALS = 5
MAX_LOG_CHARS = 800

COMPONENTS = ("store", "queue", "api", "config")
LEVELS = ("INFO", "WARN", "ERROR")


def naive_triage_enabled() -> bool:
    return os.environ.get(NAIVE_ENV) == NAIVE_VALUE


class TriageError(Exception):
    """A tool call was rejected. The message is safe to show the model."""


@dataclass
class Proposal:
    id: str
    tenant_id: str
    action: str
    params: dict[str, Any]
    status: str = "proposed"


# ---- argument models --------------------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListAlertsArgs(_Args):
    pass


class QueryMetricsArgs(_Args):
    metric: Literal[METRICS]  # type: ignore[valid-type]
    last_minutes: int = Field(default=10, ge=1, le=60)


class SearchLogsArgs(_Args):
    component: Literal[COMPONENTS] | None = None  # type: ignore[valid-type]
    level: Literal[LEVELS] | None = None  # type: ignore[valid-type]
    contains: str | None = Field(default=None, max_length=50)
    limit: int = Field(default=10, ge=1, le=20)


class MetadataChangesArgs(_Args):
    object: str | None = Field(default=None, pattern=IDENT)


class SearchRunbooksArgs(_Args):
    query: str = Field(min_length=1, max_length=200)


class ProposeArgs(_Args):
    action: str = Field(max_length=40)
    params: dict[str, Any]


class PoolParams(_Args):
    new_size: int


class WorkersParams(_Args):
    replicas: int = Field(ge=1, le=MAX_WORKERS)


class DeadLetterParams(_Args):
    message_id: str = Field(max_length=64)


class RevertParams(_Args):
    object: str = Field(pattern=IDENT)
    to_version: int = Field(ge=1)


class EscalateParams(_Args):
    rotation: str = Field(max_length=40)


ACTION_PARAMS: dict[str, type[_Args]] = {
    "increase_pool_size": PoolParams,
    "scale_workers": WorkersParams,
    "dead_letter_message": DeadLetterParams,
    "revert_metadata_change": RevertParams,
    "escalate": EscalateParams,
}

TOOL_ARGS: dict[str, type[_Args]] = {
    "list_alerts": ListAlertsArgs,
    "query_metrics": QueryMetricsArgs,
    "search_logs": SearchLogsArgs,
    "get_metadata_changes": MetadataChangesArgs,
    "search_runbooks": SearchRunbooksArgs,
    "propose_remediation": ProposeArgs,
}


def _errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or '<args>'}: {e['msg']}"
        for e in exc.errors(include_url=False)[:5]
    )


@dataclass
class TriageTools:
    env: ChaosEnvironment
    runbooks: RagService | None
    naive: bool = False
    audit: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    _proposals: dict[tuple[str, str], Proposal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.naive and not naive_triage_enabled():
            raise RuntimeError(f"naive triage needs {NAIVE_ENV}={NAIVE_VALUE}")
        if self.naive:
            log.warning("NAIVE TRIAGE MODE: agent actions are trusted and executed. Never deploy.")

    # ---- dispatch -----------------------------------------------------------------------------

    def call(self, ctx: TenantContext | None, name: str, args: dict | None) -> dict:
        if not isinstance(ctx, TenantContext):
            raise MissingTenantContext("triage tool call without a TenantContext")
        model = TOOL_ARGS.get(name)
        if model is None:
            raise TriageError(f"unknown tool {name!r}; available: {', '.join(TOOL_ARGS)}, finish")
        raw = dict(args or {})
        ignored = tuple(sorted(k for k in raw if k in TENANT_ARG_KEYS))
        supplied = [raw.pop(k) for k in ignored]
        if self.naive:
            log.warning("NAIVE TRIAGE MODE: honouring tenant args %s", ignored)
            for value in supplied:
                try:
                    ctx = TenantContext(tenant_id=str(value), subject=ctx.subject)
                except ValueError as exc:
                    raise TriageError(str(exc)) from exc
        self.audit.append((ctx.tenant_id, name, ignored))
        if ignored:
            log.warning("tenant=%s tool=%s ignored model-supplied %s", ctx.tenant_id, name, ignored)
        try:
            parsed = model.model_validate(raw)
        except ValidationError as exc:
            raise TriageError(f"invalid arguments: {_errors(exc)}") from exc
        result = getattr(self, f"_{name}")(ctx.tenant_id, parsed)
        if ignored and not self.naive:
            result["ignored_arguments"] = list(ignored)
            result["note"] = "tenant arguments are ignored; you only see this tenant"
        return result

    # ---- read tools ---------------------------------------------------------------------------

    def _list_alerts(self, tid: str, args: ListAlertsArgs) -> dict:
        return {
            "alerts": [
                {"id": a.id, "metric": a.metric, "value": a.value, "threshold": a.threshold}
                for a in self.env.alerts(tid)
            ]
        }

    def _query_metrics(self, tid: str, args: QueryMetricsArgs) -> dict:
        values = self.env.window(tid, args.metric, args.last_minutes)
        out: dict[str, Any] = {
            "metric": args.metric,
            "values": values,
            "min": min(values, default=0),
            "max": max(values, default=0),
            "mean": round(sum(values) / len(values), 4) if values else 0,
        }
        st = self.env.state[tid]
        if args.metric.startswith("store."):
            out["pool_size"] = st.pool_size
        if args.metric == "queue.backlog":
            out["workers"] = st.workers
        return out

    def _search_logs(self, tid: str, args: SearchLogsArgs) -> dict:
        needle = args.contains.lower() if args.contains else None
        hits = []
        for line in reversed(self.env.logs):
            if line.tenant_id != tid:
                continue
            if args.component and line.component != args.component:
                continue
            if args.level and line.level != args.level:
                continue
            if needle and needle not in line.message.lower():
                continue
            hits.append(
                {
                    "id": line.id,
                    "minute": line.minute,
                    "component": line.component,
                    "level": line.level,
                    "message": line.message[:MAX_LOG_CHARS],
                }
            )
            if len(hits) >= args.limit:
                break
        return {"logs": hits}

    def _get_metadata_changes(self, tid: str, args: MetadataChangesArgs) -> dict:
        changes = [
            {
                "id": c.id,
                "object": c.object,
                "version": c.version,
                "minute": c.minute,
                "summary": c.summary,
            }
            for c in reversed(self.env.changes)
            if c.tenant_id == tid and (args.object is None or c.object == args.object)
        ]
        return {"changes": changes[:20]}

    def _search_runbooks(self, tid: str, args: SearchRunbooksArgs) -> dict:
        if self.runbooks is None:
            raise TriageError("no runbook index is configured")
        try:
            hits = self.runbooks.retrieve(TenantContext(tenant_id=tid), args.query, k=3)
        except RagError as exc:
            raise TriageError(str(exc)) from exc
        return {
            "results": [
                {"doc_id": h.chunk.doc_id, "chunk_id": h.chunk.chunk_id, "text": h.chunk.text}
                for h in hits
            ]
        }

    # ---- proposals ----------------------------------------------------------------------------

    def _propose_remediation(self, tid: str, args: ProposeArgs) -> dict:
        params = dict(args.params)
        if not self.naive:
            self._validate(tid, args.action, params)
            mine = sum(1 for (t, _) in self._proposals if t == tid)
            if mine >= MAX_PROPOSALS:
                raise TriageError(f"at most {MAX_PROPOSALS} proposals per incident")
        pid = f"prop-{len(self._proposals) + 1}"
        proposal = Proposal(pid, tid, args.action, params)
        self._proposals[(tid, pid)] = proposal
        if self.naive:  # the vulnerable pattern: the agent's word is the change
            self._execute(proposal)
            proposal.status = "executed"
        return {
            "proposal_id": pid,
            "action": args.action,
            "params": params,
            "status": proposal.status,
            "note": "recorded for human approval; nothing has changed yet"
            if proposal.status == "proposed"
            else "executed",
        }

    def _validate(self, tid: str, action: str, params: dict) -> None:
        model = ACTION_PARAMS.get(action)
        if model is None:
            raise TriageError(
                f"action {action!r} is not allowed; allowed: {', '.join(ACTION_PARAMS)}"
            )
        try:
            p = model.model_validate(params)
        except ValidationError as exc:
            raise TriageError(f"invalid parameters for {action}: {_errors(exc)}") from exc
        st = self.env.state[tid]
        if isinstance(p, PoolParams):
            limit = min(2 * st.pool_size, MAX_POOL)
            if not st.pool_size < p.new_size <= limit:
                raise TriageError(f"new_size must be between {st.pool_size + 1} and {limit}")
        elif isinstance(p, DeadLetterParams):
            if all(m.id != p.message_id for m in st.queue):
                raise TriageError(f"no message {p.message_id!r} in this tenant's queue")
        elif isinstance(p, RevertParams):
            versions = [
                c.version for c in self.env.changes if c.tenant_id == tid and c.object == p.object
            ]
            if not versions or p.to_version >= max(versions):
                raise TriageError(f"no earlier version {p.to_version} of {p.object!r} to revert to")

    def _execute(self, proposal: Proposal) -> None:
        tid, a, p = proposal.tenant_id, proposal.action, proposal.params
        if a == "increase_pool_size":
            self.env.set_pool_size(tid, int(p["new_size"]))
        elif a == "scale_workers":
            self.env.set_workers(tid, int(p["replicas"]))
        elif a == "dead_letter_message":
            owner = tid
            if self.naive:  # the classic bug: look the message up by ID alone
                owner = next(
                    (
                        t
                        for t, st in self.env.state.items()
                        if any(m.id == p["message_id"] for m in st.queue)
                    ),
                    tid,
                )
            self.env.dead_letter(owner, str(p["message_id"]))
        elif a == "revert_metadata_change":
            self.env.revert_metadata(tid, str(p["object"]), int(p["to_version"]))
        # "escalate" records intent only; paging is outside the simulation.

    def approve(self, ctx: TenantContext, proposal_id: str, confirmation: Confirmation) -> dict:
        """Execute a proposal. Called by the human-facing surface, never by the agent."""
        if not isinstance(ctx, TenantContext):
            raise MissingTenantContext("approve without a TenantContext")
        proposal = self._proposals.get((ctx.tenant_id, proposal_id))
        if proposal is None or proposal.status != "proposed":
            raise TriageError(f"no pending proposal {proposal_id!r}")
        if not isinstance(confirmation, Confirmation) or confirmation.change_id != proposal_id:
            raise TriageError("approval does not match this proposal")
        self._validate(ctx.tenant_id, proposal.action, proposal.params)  # state may have moved on
        self._execute(proposal)
        proposal.status = "executed"
        log.info("tenant=%s executed %s (%s)", ctx.tenant_id, proposal_id, proposal.action)
        return {"proposal_id": proposal_id, "status": proposal.status}

    # ---- helpers for the agent loop -----------------------------------------------------------

    def all_proposals(self) -> list[Proposal]:
        return list(self._proposals.values())

    def session_proposal_ids(self, ctx: TenantContext) -> set[str]:
        return {pid for (tid, pid) in self._proposals if tid == ctx.tenant_id}

    def evidence_exists(self, ctx: TenantContext, ref: str) -> bool:
        tid = ctx.tenant_id
        if ref.startswith("alert-"):
            return any(ref == f"alert-{tid}-{m}" for m in METRICS)
        if ref.startswith("log-"):
            return any(line.id == ref and line.tenant_id == tid for line in self.env.logs)
        if ref.startswith("change-"):
            return any(c.id == ref and c.tenant_id == tid for c in self.env.changes)
        if ref.startswith("metric:"):
            return ref.removeprefix("metric:") in METRICS
        return False
