"""Stage 3 pooled index on Postgres + pgvector with row-level security."""

import pytest

from core.llm.embeddings import HashingEmbedder
from core.synthetic import synthetic_documents

pytestmark = pytest.mark.pg


@pytest.fixture
def pg(pg_dsns):
    from rag.pg_index import migrate, reset

    migrate(pg_dsns.admin)
    reset(pg_dsns.admin)
    return pg_dsns


def test_migration_is_idempotent_and_role_cannot_bypass_rls(pg):
    import psycopg
    from rag.pg_index import APP_ROLE, migrate

    migrate(pg.admin)  # second run is a no-op
    with psycopg.connect(pg.admin) as conn:
        role = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (APP_ROLE,)
        ).fetchone()
        table = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'rag_chunks'"
        ).fetchone()
    assert role == (False, False)
    assert table == (True, True)


def test_rls_without_tenant_setting_returns_nothing(pg, seeded):
    import psycopg
    from conftest import first_doc_llm
    from rag.pg_index import PooledPgIndex

    from rag import RagService

    _, tenants = seeded
    service = RagService(PooledPgIndex(pg.app), HashingEmbedder(), first_doc_llm())
    doc = synthetic_documents(tenants[0])[0]
    service.ingest(
        tenants[0].context(), {"doc_id": doc.doc_id, "title": doc.title, "text": doc.text}
    )
    service.close()
    with psycopg.connect(pg.app) as conn:
        assert conn.execute("SELECT count(*) FROM rag_chunks").fetchone()[0] == 0
    with psycopg.connect(pg.admin) as conn:
        assert conn.execute("SELECT count(*) FROM rag_chunks").fetchone()[0] > 0


def test_rls_with_check_blocks_writing_another_tenants_rows(pg):
    import psycopg

    with psycopg.connect(pg.app) as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'fjord', false)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO rag_chunks (tenant_id, doc_id, ordinal, title, text, embedding) "
                "VALUES ('cinder', 'd', 0, 't', 'x', %s::vector)",
                ("[" + ",".join(["0"] * 255 + ["1"]) + "]",),
            )


def test_pooled_and_silo_rank_the_same(pg, seeded):
    from conftest import first_doc_llm
    from rag.pg_index import PooledPgIndex

    from rag import RagService, SiloIndex

    _, tenants = seeded
    me = tenants[0]
    services = [
        RagService(SiloIndex(), HashingEmbedder(), first_doc_llm()),
        RagService(PooledPgIndex(pg.app), HashingEmbedder(), first_doc_llm()),
    ]
    rankings = []
    for service in services:
        for doc in synthetic_documents(me):
            service.ingest(
                me.context(), {"doc_id": doc.doc_id, "title": doc.title, "text": doc.text}
            )
        hits = service.retrieve(me.context(), "pool size connection saturation latency", k=3)
        rankings.append([h.chunk.chunk_id for h in hits])
        service.close()
    assert rankings[0] == rankings[1]
