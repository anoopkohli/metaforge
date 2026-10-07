"""Proposal schema: the only things the generator can ask for.

Invariant: model output becomes a `Proposal` only through `parse_proposal`, which is strict (no type
coercion, no unknown keys). The schema has no tenant field and no permission or platform actions, so
the model cannot express "write to another tenant" or "grant a role" in a form that parses.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from core.store.models import IDENT, ObjectDef, ObjectOverride


class ProposalError(ValueError):
    """Model output is not a well-formed proposal. The message is safe to show the model."""


class CreateObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["create_object"]
    object: ObjectDef


class OverrideObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["override_object"]
    target: str = Field(pattern=IDENT)
    override: ObjectOverride


class Refuse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["refuse"]
    reason: str = Field(min_length=1, max_length=500)


Proposal = Annotated[CreateObject | OverrideObject | Refuse, Field(discriminator="action")]

_ADAPTER: TypeAdapter[CreateObject | OverrideObject | Refuse] = TypeAdapter(Proposal)
_FENCED = re.compile(r"```(?:json)?\s*\n(.*?)\n?```", re.DOTALL)
_MAX_REPORTED_ERRORS = 8


def _strip_fence(text: str) -> str:
    """Accept a bare JSON object, or exactly one fenced block and nothing else."""
    stripped = text.strip()
    match = _FENCED.fullmatch(stripped)
    return match.group(1) if match else stripped


def parse_proposal(text: str) -> CreateObject | OverrideObject | Refuse:
    raw = _strip_fence(text)
    try:
        json.loads(raw)
    except ValueError as exc:
        raise ProposalError(f"response is not a single JSON object: {exc}") from exc
    try:
        return _ADAPTER.validate_json(raw, strict=True)
    except ValidationError as exc:
        lines = [
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors(include_url=False)[:_MAX_REPORTED_ERRORS]
        ]
        raise ProposalError("; ".join(lines)) from exc
