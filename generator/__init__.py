"""Stage 1: natural language -> validated metadata proposal for one tenant.

Invariant: model output is untrusted. It becomes a write only after strict parsing, semantic checks
against the caller's own view of the store, and an explicit `apply_proposal()` call that re-checks.
"""

from generator.generate import (
    MAX_DESCRIPTION_CHARS,
    Attempt,
    GenerationResult,
    InvalidDescription,
    ProposalRejected,
    apply_proposal,
    generate,
)
from generator.proposal import CreateObject, OverrideObject, ProposalError, Refuse, parse_proposal
from generator.validate import MAX_FIELDS, check_proposal, merge_override

__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "MAX_FIELDS",
    "Attempt",
    "CreateObject",
    "GenerationResult",
    "InvalidDescription",
    "OverrideObject",
    "ProposalError",
    "ProposalRejected",
    "Refuse",
    "apply_proposal",
    "check_proposal",
    "generate",
    "merge_override",
    "parse_proposal",
]
