"""Semantic checks of a parsed proposal against one tenant's current view of the store.

Invariant: a proposal is checked only against what `ctx` can see (platform objects plus its own
custom objects). Nothing here reads another tenant's data, and every check runs again at apply time.
"""

from __future__ import annotations

from core.store.models import FieldDef, ObjectOverride
from core.store.sqlite_store import MetadataStore
from core.tenancy.context import TenantContext
from generator.proposal import CreateObject, OverrideObject, Refuse

MAX_FIELDS = 40
MAX_LABEL_CHARS = 80


def merge_override(existing: ObjectOverride | None, delta: ObjectOverride) -> ObjectOverride:
    """Layer a proposed delta onto the tenant's existing override so nothing there is lost."""
    if existing is None:
        return delta
    hidden = list(dict.fromkeys([*existing.hide_fields, *delta.hide_fields]))
    return ObjectOverride(
        add_fields=[*existing.add_fields, *delta.add_fields],
        hide_fields=hidden,
        relabel={**existing.relabel, **delta.relabel},
    )


def _label_errors(where: str, label: str | None) -> list[str]:
    if label is None:
        return []
    if len(label) > MAX_LABEL_CHARS:
        return [f"{where}: label is longer than {MAX_LABEL_CHARS} characters"]
    if not label.isprintable():
        return [f"{where}: label must be a single line of printable text"]
    return []


def _field_errors(fields: list[FieldDef], visible: set[str], self_name: str | None) -> list[str]:
    errors: list[str] = []
    allowed = visible | ({self_name} if self_name else set())
    for f in fields:
        errors += _label_errors(f"field {f.name!r}", f.label)
        if f.reference_to is not None and f.reference_to not in allowed:
            errors.append(
                f"field {f.name!r} references {f.reference_to!r}, which is not an object this "
                f"tenant can see; visible objects: {', '.join(sorted(allowed))}"
            )
    return errors


def check_proposal(
    store: MetadataStore,
    ctx: TenantContext,
    proposal: CreateObject | OverrideObject | Refuse,
) -> list[str]:
    """Return problems with `proposal` for this tenant. An empty list means it can be applied."""
    visible = set(store.list_objects(ctx))  # raises MissingTenantContext without a context
    if isinstance(proposal, Refuse):
        return []

    if isinstance(proposal, CreateObject):
        obj = proposal.object
        if store.get_base_object(obj.name) is not None:
            return [
                f"{obj.name!r} is a platform object; to change it, propose an override_object "
                f"with target {obj.name!r} instead of create_object"
            ]
        if obj.name in visible:
            return [
                f"an object named {obj.name!r} already exists for this tenant; choose a new name"
            ]
        errors = _label_errors(f"object {obj.name!r}", obj.label)
        if len(obj.fields) > MAX_FIELDS:
            errors.append(f"an object may have at most {MAX_FIELDS} fields")
        return errors + _field_errors(obj.fields, visible, self_name=obj.name)

    base = store.get_base_object(proposal.target)
    if base is None:
        return [
            f"{proposal.target!r} is not a platform object; override_object only applies to "
            f"platform objects ({', '.join(sorted(visible))} are visible)"
        ]
    delta = proposal.override
    errors = [e for name, label in delta.relabel.items() for e in _label_errors(name, label)]
    errors += _field_errors(delta.add_fields, visible, self_name=None)
    merged = merge_override(store.get_override(ctx, proposal.target), delta)
    try:
        effective = merged.apply(base)
    except ValueError as exc:
        errors.append(f"override is not valid for {proposal.target!r}: {exc}")
    else:
        if len(effective.fields) > MAX_FIELDS:
            errors.append(f"an object may have at most {MAX_FIELDS} fields")
    return errors
