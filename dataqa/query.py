"""A small query language over one tenant's records, its validator and its interpreter.

Invariants:
- A query is data, never code. It is parsed strictly (no unknown keys, no type coercion) and
  interpreted in Python over the caller's records. No SQL is ever built from it.
- Validation uses the caller's *effective* schema: objects of other tenants and fields this tenant
  has hidden do not exist. Restricted fields are rejected in every clause (select, where, group_by,
  aggregate, order_by) unless the caller's token carries the `manager` role, because filtering,
  sorting or averaging on a value leaks it as surely as selecting it.
- Records are read only through `visible_records`, which reads the caller's tenant and then applies
  the caller's row scopes (`own:<object>.<field>` roles from the signed token) before anything else,
  so filters, counts and averages only ever see in-scope rows. References resolve within the tenant.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.store.models import IDENT, FieldDef, ObjectDef
from core.store.sqlite_store import MetadataStore
from core.tenancy.context import MissingTenantContext, TenantContext

log = logging.getLogger("metaforge.dataqa")

MANAGER_ROLE = "manager"
MAX_LIMIT = 100
MAX_CONDITIONS = 5
NAIVE_ENV = "METAFORGE_UNSAFE_NAIVE_DATAQA"
NAIVE_VALUE = "skip-access-checks"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def naive_dataqa_enabled() -> bool:
    return os.environ.get(NAIVE_ENV) == NAIVE_VALUE


class QueryError(ValueError):
    """The query is malformed or not allowed. The message is safe to show the caller and model."""


Scalar = str | int | float | bool


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Condition(_Strict):
    field: str = Field(pattern=IDENT)
    op: Literal["=", "!=", ">", ">=", "<", "<=", "contains", "in"]
    value: Scalar | list[Scalar]


class Aggregate(_Strict):
    fn: Literal["count", "sum", "avg", "min", "max"]
    field: str | None = Field(default=None, pattern=IDENT)


class OrderBy(_Strict):
    field: str = Field(pattern=IDENT)
    desc: bool = False


class Query(_Strict):
    object: str = Field(pattern=IDENT)
    select: list[str] = Field(default_factory=list, max_length=12)
    where: list[Condition] = Field(default_factory=list, max_length=MAX_CONDITIONS)
    group_by: str | None = Field(default=None, pattern=IDENT)
    aggregate: Aggregate | None = None
    order_by: OrderBy | None = None
    limit: int = Field(default=20, ge=1, le=MAX_LIMIT)


def _errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or '<query>'}: {e['msg']}"
        for e in exc.errors(include_url=False)[:5]
    )


def parse_query(data: dict | str) -> Query:
    try:
        if isinstance(data, str):
            return Query.model_validate_json(data, strict=True)
        return Query.model_validate(data)
    except ValidationError as exc:
        raise QueryError(f"invalid query: {_errors(exc)}") from exc


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    matched: int
    truncated: bool

    def to_dict(self) -> dict:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "matched": self.matched,
            "truncated": self.truncated,
        }


@dataclass
class _View:
    """What the caller may see of one object."""

    obj: ObjectDef
    fields: dict[str, FieldDef] = field(default_factory=dict)


def _require(ctx: TenantContext | None) -> TenantContext:
    if not isinstance(ctx, TenantContext):
        raise MissingTenantContext("data query without a TenantContext")
    return ctx


def _is_manager(ctx: TenantContext) -> bool:
    return MANAGER_ROLE in ctx.roles


def _view(store: MetadataStore, ctx: TenantContext, name: str) -> _View | None:
    obj = store.get_effective_schema(ctx, name)
    if obj is None:
        return None
    return _View(obj, {f.name: f for f in obj.fields})


def row_scope(ctx: TenantContext, object_name: str) -> list[str]:
    """Fields that must equal the caller's worker ID for a row of `object_name` to be visible."""
    prefix = f"own:{object_name}."
    return sorted(r[len(prefix) :] for r in ctx.roles if r.startswith(prefix))


def visible_records(
    store: MetadataStore, ctx: TenantContext, object_name: str, *, naive: bool = False
) -> list[dict]:
    records = store.list_records(ctx, object_name)
    scope = [] if naive else row_scope(ctx, object_name)
    if not scope:
        return records
    return [r for r in records if any(r.get(f) == ctx.subject for f in scope)]


def _display_field(view: _View) -> str | None:
    for f in view.obj.fields:
        if f.type == "string" and not f.restricted and f.required:
            return f.name
    for f in view.obj.fields:
        if f.type == "string" and not f.restricted:
            return f.name
    return None


_ID_FIELD = FieldDef(name="id", type="string", label="Record ID")

# ---- validation -------------------------------------------------------------------------------

_NUMERIC = {"integer", "decimal"}
_ORDERED = _NUMERIC | {"date"}


def _check_value(f: FieldDef, op: str, value: Any) -> str | None:
    values = value if isinstance(value, list) else [value]
    if op == "in" and not isinstance(value, list):
        return f"'in' on {f.name!r} needs a list of values"
    if op != "in" and isinstance(value, list):
        return f"only 'in' takes a list (field {f.name!r})"
    if op in (">", ">=", "<", "<=") and f.type not in _ORDERED:
        return f"{op!r} needs a number or date field; {f.name!r} is {f.type}"
    if op == "contains" and f.type not in ("string", "reference"):
        return f"'contains' needs a text field; {f.name!r} is {f.type}"
    for v in values:
        ok = {
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "decimal": isinstance(v, int | float) and not isinstance(v, bool),
            "boolean": isinstance(v, bool),
            "date": isinstance(v, str) and bool(_DATE.match(v)),
            "string": isinstance(v, str),
            "reference": isinstance(v, str),
        }[f.type]
        if not ok:
            hint = " (use YYYY-MM-DD)" if f.type == "date" else ""
            return f"value {v!r} does not fit {f.name!r} ({f.type}){hint}"
    return None


def validate(
    store: MetadataStore, ctx: TenantContext, q: Query, *, naive: bool = False
) -> list[str]:
    """Problems with `q` for this caller. Empty means it may run."""
    ctx = _require(ctx)
    view = _view(store, ctx, q.object)
    if view is None:
        return [f"no object named {q.object!r}"]
    errors: list[str] = []
    manager = _is_manager(ctx) or naive

    def known(name: str, clause: str) -> FieldDef | None:
        if name == "id":  # every record's ID; needed to propose updates, never sensitive
            return _ID_FIELD
        f = view.fields.get(name)
        if f is None:
            errors.append(f"{clause}: {q.object!r} has no field {name!r}")
            return None
        if f.restricted and not manager:
            errors.append(
                f"{clause}: field {name!r} is restricted to managers and cannot be used in any "
                "part of a query (select, filter, group, aggregate or sort) by this user"
            )
            return None
        return f

    for name in q.select:
        known(name, "select")
    for cond in q.where:
        f = known(cond.field, "where")
        if f is not None and (problem := _check_value(f, cond.op, cond.value)):
            errors.append(f"where: {problem}")
    if q.group_by:
        known(q.group_by, "group_by")
        if not q.aggregate:
            errors.append("group_by needs an aggregate (for example count)")
    if q.aggregate:
        if q.aggregate.fn == "count":
            if q.aggregate.field:
                known(q.aggregate.field, "aggregate")
        elif not q.aggregate.field:
            errors.append(f"{q.aggregate.fn} needs a field")
        else:
            f = known(q.aggregate.field, "aggregate")
            if f is not None and q.aggregate.fn in ("sum", "avg") and f.type not in _NUMERIC:
                errors.append(f"{q.aggregate.fn} needs a number field; {f.name!r} is {f.type}")
            if f is not None and q.aggregate.fn in ("min", "max") and f.type not in _ORDERED:
                errors.append(f"{q.aggregate.fn} needs a number or date field")
    if q.order_by:
        allowed_virtual = q.aggregate is not None and q.order_by.field == "value"
        if not allowed_virtual:
            if q.aggregate and q.order_by.field != q.group_by:
                errors.append("with an aggregate, order_by must be 'value' or the group_by field")
            else:
                known(q.order_by.field, "order_by")
    return errors


# ---- execution --------------------------------------------------------------------------------


def _matches(rec: dict, cond: Condition, display: dict[str, dict[str, str]]) -> bool:
    raw = rec.get(cond.field)
    shown = display.get(cond.field, {}).get(raw, raw) if isinstance(raw, str) else raw
    v = cond.value
    if raw is None:
        return cond.op == "!=" and v is not None
    if cond.op == "contains":
        return str(v).lower() in str(shown).lower()
    if cond.op == "in":
        return raw in v or shown in v
    if cond.op in ("=", "!="):
        equal = raw == v or shown == v
        return equal if cond.op == "=" else not equal
    try:
        return {">": raw > v, ">=": raw >= v, "<": raw < v, "<=": raw <= v}[cond.op]
    except TypeError:
        return False


def _round(x: Any) -> Any:
    return round(x, 2) if isinstance(x, float) else x


def execute(
    store: MetadataStore,
    ctx: TenantContext,
    q: Query,
    *,
    naive: bool = False,
) -> QueryResult:
    ctx = _require(ctx)
    errors = validate(store, ctx, q, naive=naive)
    if errors:
        raise QueryError("; ".join(errors))
    view = _view(store, ctx, q.object)
    assert view is not None  # validated above
    records = visible_records(store, ctx, q.object, naive=naive)

    # Reference fields show the referenced record's display value, looked up in this tenant only.
    display: dict[str, dict[str, str]] = {}
    for f in view.obj.fields:
        if f.type == "reference" and f.reference_to:
            target = _view(store, ctx, f.reference_to)
            shown = _display_field(target) if target else None
            if shown:
                display[f.name] = {
                    r["id"]: str(r.get(shown, r["id"]))
                    for r in store.list_records(ctx, f.reference_to)
                }

    def show(rec: dict, name: str) -> Any:
        value = rec.get(name)
        if isinstance(value, str) and name in display:
            return display[name].get(value, value)
        return value

    rows = [r for r in records if all(_matches(r, c, display) for c in q.where)]
    matched = len(rows)

    if q.aggregate:
        fn, fld = q.aggregate.fn, q.aggregate.field
        groups: dict[Any, list[dict]] = defaultdict(list)
        for r in rows:
            groups[show(r, q.group_by) if q.group_by else None].append(r)
        if not q.group_by and not groups:
            groups[None] = []
        out = []
        for key, members in groups.items():
            vals = [m.get(fld) for m in members if m.get(fld) is not None] if fld else members
            if fn == "count":
                value: Any = len(vals)
            elif not vals:
                value = None
            elif fn == "sum":
                value = sum(vals)
            elif fn == "avg":
                value = sum(vals) / len(vals)
            else:
                value = min(vals) if fn == "min" else max(vals)
            row = {q.group_by: key} if q.group_by else {}
            row["value"] = _round(value)
            out.append(row)
        columns = ([q.group_by] if q.group_by else []) + ["value"]
        key_field = q.order_by.field if q.order_by else (q.group_by or "value")
        desc = q.order_by.desc if q.order_by else False
        out.sort(key=lambda r: (r.get(key_field) is None, r.get(key_field)), reverse=desc)
        return QueryResult(columns, out[: q.limit], len(out), len(out) > q.limit)

    columns = (
        list(q.select)
        or [f.name for f in view.obj.fields if not f.restricted or _is_manager(ctx) or naive][:8]
    )
    if q.order_by:
        rows.sort(
            key=lambda r: (r.get(q.order_by.field) is None, show(r, q.order_by.field)),
            reverse=q.order_by.desc,
        )
    out_rows = [{c: _round(show(r, c)) for c in columns} for r in rows[: q.limit]]
    return QueryResult(columns, out_rows, matched, matched > q.limit)


# ---- schema and descriptions ------------------------------------------------------------------


def schema_for(store: MetadataStore, ctx: TenantContext) -> list[dict]:
    """Objects this caller can query: fields marked restricted / readable, and record counts."""
    ctx = _require(ctx)
    manager = _is_manager(ctx)
    out = []
    for name in store.list_objects(ctx):
        view = _view(store, ctx, name)
        if view is None or store.count_records(ctx, name) == 0:
            continue
        scope = row_scope(ctx, name)
        out.append(
            {
                "name": name,
                "label": view.obj.label,
                "records": len(visible_records(store, ctx, name)),
                "scope": ", ".join(scope) or None,
                "fields": [
                    {
                        "name": f.name,
                        "type": f.type,
                        "reference_to": f.reference_to,
                        "restricted": f.restricted,
                        "readable": manager or not f.restricted,
                    }
                    for f in view.obj.fields
                ],
            }
        )
    return out


_FN = {"count": "number of", "sum": "total", "avg": "average", "min": "lowest", "max": "highest"}


def describe(q: Query) -> str:
    """One plain-English line describing what a query asks for."""
    if q.aggregate:
        what = (
            f"{_FN[q.aggregate.fn]} {q.object} records"
            if q.aggregate.fn == "count" and not q.aggregate.field
            else f"{_FN[q.aggregate.fn]} {q.aggregate.field or ''} of {q.object}".replace("  ", " ")
        )
    else:
        what = f"{', '.join(q.select) if q.select else 'records'} of {q.object}"
    parts = [what[0].upper() + what[1:]]
    if q.where:
        conds = [f"{c.field} {c.op} {json.dumps(c.value)}".replace('"', "") for c in q.where]
        parts.append("where " + " and ".join(conds))
    if q.group_by:
        parts.append(f"grouped by {q.group_by}")
    if q.order_by:
        parts.append(f"sorted by {q.order_by.field}{' (highest first)' if q.order_by.desc else ''}")
    parts.append(f"(up to {q.limit} rows)")
    return ", ".join(parts[:-1]) + " " + parts[-1]
