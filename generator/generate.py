"""Generate a proposal from a description, and apply an accepted proposal.

Invariants:
- `generate()` never writes. It calls the model at most `max_attempts` times and returns either a
  proposal that passed every check or `ok=False`.
- `apply_proposal()` re-runs every check against current state, then writes only through
  tenant-scoped store methods with the caller's `TenantContext`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.llm.base import LLMClient, Message, Usage
from core.obs.tracing import span
from core.store.sqlite_store import MetadataStore
from core.tenancy.context import MissingTenantContext, TenantContext
from generator.prompt import build_system_prompt, render_feedback, render_request
from generator.proposal import CreateObject, OverrideObject, ProposalError, Refuse, parse_proposal
from generator.validate import check_proposal, merge_override

MAX_DESCRIPTION_CHARS = 2000
MAX_OUTPUT_TOKENS = 2048

ProposalT = CreateObject | OverrideObject | Refuse


class InvalidDescription(ValueError):
    """The description was rejected before any model call."""


class ProposalRejected(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Attempt:
    text: str
    errors: tuple[str, ...]


@dataclass(frozen=True)
class GenerationResult:
    proposal: ProposalT | None
    attempts: list[Attempt] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.proposal is not None


def generate(
    llm: LLMClient,
    store: MetadataStore,
    ctx: TenantContext,
    description: str,
    *,
    max_attempts: int = 3,
) -> GenerationResult:
    if not isinstance(ctx, TenantContext):
        raise MissingTenantContext("generate() needs a TenantContext")
    if not description.strip():
        raise InvalidDescription("description is empty")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise InvalidDescription(f"description is longer than {MAX_DESCRIPTION_CHARS} characters")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    system = build_system_prompt(store, ctx)
    messages = [Message("user", render_request(description))]
    attempts: list[Attempt] = []
    in_tok = out_tok = 0
    start = time.perf_counter()

    with span("generator.generate", max_attempts=max_attempts):
        for _ in range(max_attempts):
            resp = llm.complete(messages, system=system, max_tokens=MAX_OUTPUT_TOKENS)
            in_tok += resp.usage.input_tokens
            out_tok += resp.usage.output_tokens
            proposal: ProposalT | None = None
            try:
                proposal = parse_proposal(resp.text)
                errors = check_proposal(store, ctx, proposal)
            except ProposalError as exc:
                errors = [str(exc)]
            attempts.append(Attempt(resp.text, tuple(errors)))
            if not errors:
                return GenerationResult(
                    proposal, attempts, Usage(in_tok, out_tok), time.perf_counter() - start
                )
            messages += [Message("assistant", resp.text), Message("user", render_feedback(errors))]

    return GenerationResult(None, attempts, Usage(in_tok, out_tok), time.perf_counter() - start)


def apply_proposal(store: MetadataStore, ctx: TenantContext, proposal: ProposalT) -> int:
    """Write an accepted proposal for `ctx`. Returns the stored version."""
    if isinstance(proposal, Refuse):
        raise ProposalRejected([f"nothing to apply: the request was refused ({proposal.reason})"])
    errors = check_proposal(store, ctx, proposal)
    if errors:
        raise ProposalRejected(errors)
    with span("generator.apply", action=proposal.action):
        if isinstance(proposal, CreateObject):
            return store.put_object(ctx, proposal.object)
        merged = merge_override(store.get_override(ctx, proposal.target), proposal.override)
        return store.put_override(ctx, proposal.target, merged)
