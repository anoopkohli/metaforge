"""SQLite-backed metadata store.

Invariant: every statement that touches a tenant_* table is parameterised with the tenant ID of an
explicit `TenantContext`. There is no method that reads tenant data without one.

Shared platform data lives in `base_objects` and is readable by every tenant. It is written only
through the `platform_*` methods, which exist for seeding and admin tooling, not request paths.

Stage 0 uses SQLite so the project runs offline with zero setup. The same interface moves to
Postgres (with row-level security as a second layer) in stage 3.
"""

from __future__ import annotations

import sqlite3
import threading
import time

from core.store.models import ObjectDef, ObjectOverride
from core.tenancy.context import MissingTenantContext, TenantContext

_SCHEMA = """
CREATE TABLE IF NOT EXISTS base_objects (
    name        TEXT PRIMARY KEY,
    definition  TEXT NOT NULL,
    version     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tenant_objects (
    tenant_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    definition  TEXT NOT NULL,
    version     INTEGER NOT NULL,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (tenant_id, name)
);
CREATE TABLE IF NOT EXISTS tenant_overrides (
    tenant_id    TEXT NOT NULL,
    object_name  TEXT NOT NULL,
    override     TEXT NOT NULL,
    version      INTEGER NOT NULL,
    updated_at   REAL NOT NULL,
    PRIMARY KEY (tenant_id, object_name)
);
"""


class ObjectNameConflict(ValueError):
    """A tenant tried to define a custom object whose name is already a platform base object."""


class ObjectNotFound(LookupError):
    pass


def _tenant_id(ctx: TenantContext | None) -> str:
    if ctx is None:
        raise MissingTenantContext("store call without a TenantContext")
    if not isinstance(ctx, TenantContext):
        raise TypeError("ctx must be a TenantContext")
    return ctx.tenant_id


class MetadataStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- platform level (seeding / admin only; never call from request paths) -------------

    def platform_put_base_object(self, obj: ObjectDef) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT version FROM base_objects WHERE name = ?", (obj.name,)
            ).fetchone()
            version = (row[0] + 1) if row else 1
            self._db.execute(
                "INSERT INTO base_objects (name, definition, version) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET definition = excluded.definition, "
                "version = excluded.version",
                (obj.name, obj.model_dump_json(), version),
            )
            self._db.commit()
            return version

    def platform_list_tenant_ids(self) -> list[str]:
        """Admin/test helper: which tenants have any data. Not available to request paths."""
        with self._lock:
            rows = self._db.execute(
                "SELECT tenant_id FROM tenant_objects UNION SELECT tenant_id FROM tenant_overrides"
            ).fetchall()
        return sorted(r[0] for r in rows)

    # ---- shared, read-only for tenants -----------------------------------------------------

    def get_base_object(self, name: str) -> ObjectDef | None:
        with self._lock:
            row = self._db.execute(
                "SELECT definition FROM base_objects WHERE name = ?", (name,)
            ).fetchone()
        return ObjectDef.model_validate_json(row[0]) if row else None

    # ---- tenant custom objects -------------------------------------------------------------

    def put_object(self, ctx: TenantContext, obj: ObjectDef) -> int:
        tid = _tenant_id(ctx)
        if self.get_base_object(obj.name) is not None:
            raise ObjectNameConflict(f"{obj.name!r} is a platform object; use an override")
        with self._lock:
            row = self._db.execute(
                "SELECT version FROM tenant_objects WHERE tenant_id = ? AND name = ?",
                (tid, obj.name),
            ).fetchone()
            version = (row[0] + 1) if row else 1
            self._db.execute(
                "INSERT INTO tenant_objects (tenant_id, name, definition, version, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, name) DO UPDATE SET definition = excluded.definition, "
                "version = excluded.version, updated_at = excluded.updated_at",
                (tid, obj.name, obj.model_dump_json(), version, time.time()),
            )
            self._db.commit()
            return version

    def get_object(self, ctx: TenantContext, name: str) -> ObjectDef | None:
        tid = _tenant_id(ctx)
        with self._lock:
            row = self._db.execute(
                "SELECT definition FROM tenant_objects WHERE tenant_id = ? AND name = ?",
                (tid, name),
            ).fetchone()
        return ObjectDef.model_validate_json(row[0]) if row else None

    def delete_object(self, ctx: TenantContext, name: str) -> bool:
        tid = _tenant_id(ctx)
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM tenant_objects WHERE tenant_id = ? AND name = ?", (tid, name)
            )
            self._db.commit()
            return cur.rowcount > 0

    # ---- tenant overrides on platform objects ----------------------------------------------

    def put_override(self, ctx: TenantContext, object_name: str, override: ObjectOverride) -> int:
        tid = _tenant_id(ctx)
        base = self.get_base_object(object_name)
        if base is None:
            raise ObjectNotFound(f"no platform object {object_name!r}")
        override.apply(base)  # validate against base before storing; raises ValueError
        with self._lock:
            row = self._db.execute(
                "SELECT version FROM tenant_overrides WHERE tenant_id = ? AND object_name = ?",
                (tid, object_name),
            ).fetchone()
            version = (row[0] + 1) if row else 1
            self._db.execute(
                "INSERT INTO tenant_overrides (tenant_id, object_name, override, version, "
                "updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, object_name) DO UPDATE SET override = excluded.override, "
                "version = excluded.version, updated_at = excluded.updated_at",
                (tid, object_name, override.model_dump_json(), version, time.time()),
            )
            self._db.commit()
            return version

    def get_override(self, ctx: TenantContext, object_name: str) -> ObjectOverride | None:
        tid = _tenant_id(ctx)
        with self._lock:
            row = self._db.execute(
                "SELECT override FROM tenant_overrides WHERE tenant_id = ? AND object_name = ?",
                (tid, object_name),
            ).fetchone()
        return ObjectOverride.model_validate_json(row[0]) if row else None

    # ---- effective view --------------------------------------------------------------------

    def get_effective_schema(self, ctx: TenantContext, name: str) -> ObjectDef | None:
        """What this tenant actually sees: its custom object, or base + its override."""
        custom = self.get_object(ctx, name)
        if custom is not None:
            return custom
        base = self.get_base_object(name)
        if base is None:
            return None
        override = self.get_override(ctx, name)
        return override.apply(base) if override else base

    def list_objects(self, ctx: TenantContext) -> list[str]:
        tid = _tenant_id(ctx)
        with self._lock:
            custom = self._db.execute(
                "SELECT name FROM tenant_objects WHERE tenant_id = ?", (tid,)
            ).fetchall()
            base = self._db.execute("SELECT name FROM base_objects").fetchall()
        return sorted({r[0] for r in custom} | {r[0] for r in base})
