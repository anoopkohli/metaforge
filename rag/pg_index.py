"""Pooled index: one shared Postgres table with pgvector, isolated in two layers.

Invariants:
- Application layer: every statement filters on the caller's tenant ID (naive mode removes this).
- Database layer: the table has row-level security, forced, keyed on the transaction-local setting
  `app.tenant_id`. The application connects as `metaforge_app`, which is not the owner, not a
  superuser and cannot bypass RLS. With no `app.tenant_id` set, the policy matches no rows.
- `app.tenant_id` is set with `set_config(..., true)` inside each transaction, so it cannot leak to
  the next transaction on the same connection.

Migrations run as the admin (owner) role; the app role only gets row privileges on `rag_chunks`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from core.tenancy.context import MissingTenantContext, TenantContext
from rag.models import Chunk, Hit, require_naive

APP_ROLE = "metaforge_app"
DIM = 256
LOCAL_PORT = int(os.environ.get("METAFORGE_PG_PORT", "54329"))
LOCAL_DATA = Path(__file__).resolve().parents[1] / ".pgdata"

_MIGRATION = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS rag_chunks (
    tenant_id  text NOT NULL,
    doc_id     text NOT NULL,
    ordinal    integer NOT NULL,
    title      text NOT NULL,
    text       text NOT NULL,
    embedding  vector({DIM}) NOT NULL,
    PRIMARY KEY (tenant_id, doc_id, ordinal)
);
ALTER TABLE rag_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_chunks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON rag_chunks;
CREATE POLICY tenant_isolation ON rag_chunks
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO {APP_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON rag_chunks TO {APP_ROLE};
"""


@dataclass(frozen=True)
class Dsns:
    admin: str
    app: str


def _reachable(dsn: str) -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except psycopg.Error:
        return False


@lru_cache(maxsize=1)
def discover_dsns() -> Dsns | None:
    """DSNs from METAFORGE_PG_ADMIN_DSN / METAFORGE_PG_APP_DSN, else the project-local cluster."""
    admin = os.environ.get("METAFORGE_PG_ADMIN_DSN")
    app = os.environ.get("METAFORGE_PG_APP_DSN")
    if admin and app:
        dsns = Dsns(admin, app)
    elif (LOCAL_DATA / "postmaster.pid").exists():
        base = f"host={LOCAL_DATA} port={LOCAL_PORT} dbname=metaforge"
        dsns = Dsns(f"{base} user=metaforge_admin", f"{base} user={APP_ROLE}")
    else:
        return None
    return dsns if _reachable(dsns.admin) else None


def migrate(admin_dsn: str) -> None:
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(7331)")  # serialise concurrent migrations
        try:
            conn.execute(_MIGRATION)
        finally:
            conn.execute("SELECT pg_advisory_unlock(7331)")


def reset(admin_dsn: str) -> None:
    """Test helper: remove every row (as the owner, so RLS does not apply)."""
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE rag_chunks")


def _vector(values: Sequence[float]) -> str:
    if len(values) != DIM:
        raise ValueError(f"embedding has {len(values)} dimensions; the index needs {DIM}")
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


def _tenant_id(ctx: TenantContext | None) -> str:
    if not isinstance(ctx, TenantContext):
        raise MissingTenantContext("index call without a TenantContext")
    return ctx.tenant_id


class PooledPgIndex:
    def __init__(self, dsn: str, *, naive: bool = False) -> None:
        import psycopg

        if naive:
            require_naive("PooledPgIndex")
        self.naive = naive
        # autocommit: the explicit transaction() blocks below are the only transactions.
        self._conn = psycopg.connect(dsn, autocommit=True)

    @contextmanager
    def _scoped(self, tid: str) -> Iterator[None]:
        """A transaction with `app.tenant_id` set for its duration only."""
        with self._conn.transaction():
            self._conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tid,))
            yield

    def upsert_document(
        self, ctx: TenantContext, doc_id: str, title: str, chunks: Sequence[tuple[str, list[float]]]
    ) -> int:
        tid = _tenant_id(ctx)
        with self._scoped(tid):
            if self.naive:  # the classic bug: "delete the old version" forgets the tenant
                self._conn.execute("DELETE FROM rag_chunks WHERE doc_id = %s", (doc_id,))
            else:
                self._conn.execute(
                    "DELETE FROM rag_chunks WHERE tenant_id = %s AND doc_id = %s", (tid, doc_id)
                )
            with self._conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO rag_chunks (tenant_id, doc_id, ordinal, title, text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s::vector)",
                    [
                        (tid, doc_id, i, title, text, _vector(vec))
                        for i, (text, vec) in enumerate(chunks)
                    ],
                )
        return len(chunks)

    def delete_document(self, ctx: TenantContext, doc_id: str) -> bool:
        tid = _tenant_id(ctx)
        with self._scoped(tid):
            if self.naive:
                cur = self._conn.execute("DELETE FROM rag_chunks WHERE doc_id = %s", (doc_id,))
            else:
                cur = self._conn.execute(
                    "DELETE FROM rag_chunks WHERE tenant_id = %s AND doc_id = %s", (tid, doc_id)
                )
            return cur.rowcount > 0

    def search(self, ctx: TenantContext, vector: Sequence[float], k: int) -> list[Hit]:
        tid = _tenant_id(ctx)
        q = _vector(vector)
        cols = "tenant_id, doc_id, ordinal, title, text, 1 - (embedding <=> %s::vector) AS score"
        order = "ORDER BY embedding <=> %s::vector, doc_id, ordinal LIMIT %s"
        with self._scoped(tid):
            if self.naive:
                rows = self._conn.execute(
                    f"SELECT {cols} FROM rag_chunks {order}", (q, q, k)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"SELECT {cols} FROM rag_chunks WHERE tenant_id = %s {order}", (q, tid, q, k)
                ).fetchall()
        return [Hit(Chunk(r[0], r[1], r[2], r[3], r[4]), float(r[5])) for r in rows]

    def close(self) -> None:
        self._conn.close()
