"""Append-only, hash-chained audit log, one chain per tenant.

Invariants:
- Entries are only ever appended. Each entry's hash covers its own fields and the previous entry's
  hash, so editing, deleting or reordering any entry breaks every hash after it, and `verify`
  reports the first broken one. This detects tampering; it does not prevent it (anyone with
  database access can still write). Anchoring the latest hash outside the database is future work.
- Reads and verification are tenant-scoped and need the `manager` role.
- Details are small and flat (strings, numbers, booleans, short lists), and callers pass actions,
  names, counts and outcomes, never record values.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from core.tenancy.context import MissingTenantContext, TenantContext

MANAGER = "manager"
GENESIS = "0" * 64
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_MAX_STR = 500
_MAX_LIST = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    tenant_id  TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    ts         REAL NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL,
    PRIMARY KEY (tenant_id, seq)
);
"""


class AuditAccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class AuditEntry:
    tenant_id: str
    seq: int
    ts: float
    actor: str
    action: str
    detail: dict[str, Any]
    prev_hash: str
    hash: str

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "actor": self.actor,
            "action": self.action,
            "detail": self.detail,
            "hash": self.hash[:12],
        }


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_bad: int | None
    reason: str = ""


def _require(ctx: TenantContext | None) -> TenantContext:
    if not isinstance(ctx, TenantContext):
        raise MissingTenantContext("audit call without a TenantContext")
    return ctx


def _check_detail(detail: dict) -> str:
    if not isinstance(detail, dict):
        raise ValueError("audit detail must be a dict")
    for key, value in detail.items():
        if not isinstance(key, str) or not _KEY.match(key):
            raise ValueError(f"bad audit detail key {key!r}")
        items = value if isinstance(value, list) else [value]
        if isinstance(value, list) and len(value) > _MAX_LIST:
            raise ValueError(f"audit detail list {key!r} is too long")
        for item in items:
            if isinstance(item, dict | list | tuple | set):
                raise ValueError(f"audit detail {key!r} must be flat")
            if isinstance(item, str) and len(item) > _MAX_STR:
                raise ValueError(f"audit detail {key!r} is too long")
            if item is not None and not isinstance(item, str | int | float | bool):
                raise ValueError(f"audit detail {key!r} has an unsupported type")
    return json.dumps(detail, sort_keys=True, separators=(",", ":"))


def _hash(prev: str, seq: int, ts: float, tenant: str, actor: str, action: str, detail: str) -> str:
    material = "|".join([prev, str(seq), repr(ts), tenant, actor, action, detail])
    return hashlib.sha256(material.encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def append(self, ctx: TenantContext, action: str, detail: dict | None = None) -> AuditEntry:
        ctx = _require(ctx)
        body = _check_detail(detail or {})
        with self._lock:
            row = self._db.execute(
                "SELECT seq, hash FROM audit_log WHERE tenant_id = ? ORDER BY seq DESC LIMIT 1",
                (ctx.tenant_id,),
            ).fetchone()
            seq, prev = (row[0] + 1, row[1]) if row else (1, GENESIS)
            ts = round(time.time(), 6)
            digest = _hash(prev, seq, ts, ctx.tenant_id, ctx.subject, action, body)
            self._db.execute(
                "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ctx.tenant_id, seq, ts, ctx.subject, action, body, prev, digest),
            )
            self._db.commit()
        return AuditEntry(
            ctx.tenant_id, seq, ts, ctx.subject, action, json.loads(body), prev, digest
        )

    def _rows(self, tenant_id: str) -> list[tuple]:
        with self._lock:
            return self._db.execute(
                "SELECT seq, ts, actor, action, detail, prev_hash, hash FROM audit_log "
                "WHERE tenant_id = ? ORDER BY seq",
                (tenant_id,),
            ).fetchall()

    def _require_manager(self, ctx: TenantContext | None) -> TenantContext:
        ctx = _require(ctx)
        if MANAGER not in ctx.roles:
            raise AuditAccessDenied("the audit log is available to managers only")
        return ctx

    def entries(self, ctx: TenantContext, limit: int = 200) -> list[AuditEntry]:
        ctx = self._require_manager(ctx)
        rows = self._rows(ctx.tenant_id)[-limit:]
        return [
            AuditEntry(ctx.tenant_id, s, ts, a, act, json.loads(d), p, h)
            for s, ts, a, act, d, p, h in reversed(rows)
        ]

    def verify(self, ctx: TenantContext) -> VerifyResult:
        ctx = self._require_manager(ctx)
        prev, expected_seq = GENESIS, 1
        rows = self._rows(ctx.tenant_id)
        for seq, ts, actor, action, detail, prev_hash, digest in rows:
            if seq != expected_seq:
                return VerifyResult(
                    False, expected_seq - 1, expected_seq, "entry missing or out of order"
                )
            if prev_hash != prev:
                return VerifyResult(False, seq - 1, seq, "link to the previous entry is broken")
            if _hash(prev, seq, ts, ctx.tenant_id, actor, action, detail) != digest:
                return VerifyResult(False, seq - 1, seq, "entry was modified")
            prev, expected_seq = digest, seq + 1
        return VerifyResult(True, len(rows), None)
