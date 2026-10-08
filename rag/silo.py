"""Siloed index: one SQLite database per tenant.

Invariant: a tenant's handle opens only `<root>/<tenant_id>.sqlite` (or a private in-memory
database), and each database records the tenant it belongs to; opening it for anyone else is an
error. Search is brute-force cosine over that tenant's chunks, fine for the small corpora used here.
"""

from __future__ import annotations

import sqlite3
import threading
from array import array
from collections.abc import Sequence
from pathlib import Path

from core.tenancy.context import MissingTenantContext, TenantContext
from rag.models import Chunk, Hit, require_naive

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks (
    doc_id     TEXT NOT NULL,
    ordinal    INTEGER NOT NULL,
    title      TEXT NOT NULL,
    text       TEXT NOT NULL,
    embedding  BLOB NOT NULL,
    PRIMARY KEY (doc_id, ordinal)
);
"""


def _tenant_id(ctx: TenantContext | None) -> str:
    if not isinstance(ctx, TenantContext):
        raise MissingTenantContext("index call without a TenantContext")
    return ctx.tenant_id


class SiloIndex:
    def __init__(self, root: str | Path | None = None, *, naive: bool = False) -> None:
        if naive:
            # Naive mode has nothing to switch off here: isolation is the file layout itself.
            require_naive("SiloIndex")
        self.naive = naive
        self._root = Path(root).resolve() if root is not None else None
        self._dbs: dict[str, sqlite3.Connection] = {}
        self._lock = threading.RLock()

    def _db(self, ctx: TenantContext) -> sqlite3.Connection:
        tid = _tenant_id(ctx)
        with self._lock:
            if tid in self._dbs:
                return self._dbs[tid]
            if self._root is None:
                target = ":memory:"
            else:
                self._root.mkdir(parents=True, exist_ok=True)
                path = (self._root / f"{tid}.sqlite").resolve()
                if path.parent != self._root:  # unreachable with validated IDs; kept as a tripwire
                    raise ValueError("tenant database path escapes the silo root")
                target = str(path)
            db = sqlite3.connect(target, check_same_thread=False)
            db.executescript(_SCHEMA)
            owner = db.execute("SELECT value FROM meta WHERE key = 'tenant_id'").fetchone()
            if owner is None:
                db.execute("INSERT INTO meta (key, value) VALUES ('tenant_id', ?)", (tid,))
                db.commit()
            elif owner[0] != tid:
                db.close()
                raise RuntimeError("silo database belongs to a different tenant")
            self._dbs[tid] = db
            return db

    def upsert_document(
        self, ctx: TenantContext, doc_id: str, title: str, chunks: Sequence[tuple[str, list[float]]]
    ) -> int:
        db = self._db(ctx)
        with self._lock:
            db.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            db.executemany(
                "INSERT INTO chunks (doc_id, ordinal, title, text, embedding) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (doc_id, i, title, text, array("f", vec).tobytes())
                    for i, (text, vec) in enumerate(chunks)
                ],
            )
            db.commit()
        return len(chunks)

    def delete_document(self, ctx: TenantContext, doc_id: str) -> bool:
        db = self._db(ctx)
        with self._lock:
            cur = db.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            db.commit()
            return cur.rowcount > 0

    def search(self, ctx: TenantContext, vector: Sequence[float], k: int) -> list[Hit]:
        tid = _tenant_id(ctx)
        db = self._db(ctx)
        query = array("f", vector)
        with self._lock:
            rows = db.execute(
                "SELECT doc_id, ordinal, title, text, embedding FROM chunks"
            ).fetchall()
        scored = []
        for doc_id, ordinal, title, text, blob in rows:
            emb = array("f")
            emb.frombytes(blob)
            score = sum(a * b for a, b in zip(query, emb, strict=True))
            scored.append(
                (-score, doc_id, ordinal, Hit(Chunk(tid, doc_id, ordinal, title, text), score))
            )
        scored.sort(key=lambda s: s[:3])
        return [s[3] for s in scored[:k]]

    def close(self) -> None:
        with self._lock:
            for db in self._dbs.values():
                db.close()
            self._dbs.clear()
