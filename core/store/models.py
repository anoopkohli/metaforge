"""Metadata models: object definitions, fields, and per-tenant overrides."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IDENT = r"^[a-z][a-z0-9_]{0,62}$"

FieldType = Literal["string", "integer", "decimal", "boolean", "date", "reference"]


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=IDENT)
    type: FieldType
    required: bool = False
    label: str | None = None
    reference_to: str | None = Field(default=None, pattern=IDENT)

    @model_validator(mode="after")
    def _reference_consistency(self) -> FieldDef:
        if self.type == "reference" and not self.reference_to:
            raise ValueError("reference fields need reference_to")
        if self.type != "reference" and self.reference_to:
            raise ValueError("reference_to is only valid on reference fields")
        return self


class ObjectDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=IDENT)
    label: str | None = None
    fields: list[FieldDef]

    @model_validator(mode="after")
    def _unique_fields(self) -> ObjectDef:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("duplicate field names")
        if not names:
            raise ValueError("an object needs at least one field")
        return self


class ObjectOverride(BaseModel):
    """A tenant's changes on top of a platform base object."""

    model_config = ConfigDict(extra="forbid")

    add_fields: list[FieldDef] = Field(default_factory=list)
    hide_fields: list[str] = Field(default_factory=list)
    relabel: dict[str, str] = Field(default_factory=dict)

    def apply(self, base: ObjectDef) -> ObjectDef:
        """Return the effective object. Raises ValueError if this override is invalid for `base`."""
        by_name = {f.name: f for f in base.fields}

        for name in self.hide_fields:
            if name not in by_name:
                raise ValueError(f"cannot hide unknown field {name!r}")
            if by_name[name].required:
                raise ValueError(f"cannot hide required field {name!r}")
        for f in self.add_fields:
            if f.name in by_name:
                raise ValueError(f"cannot add field {f.name!r}: already defined on base")
        for name in self.relabel:
            if name not in by_name:
                raise ValueError(f"cannot relabel unknown field {name!r}")

        hidden = set(self.hide_fields)
        merged: list[FieldDef] = []
        for f in base.fields:
            if f.name in hidden:
                continue
            if f.name in self.relabel:
                f = f.model_copy(update={"label": self.relabel[f.name]})
            merged.append(f)
        merged.extend(self.add_fields)
        return ObjectDef(name=base.name, label=base.label, fields=merged)
