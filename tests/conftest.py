import pytest

from core.store.sqlite_store import MetadataStore
from core.synthetic import seed_demo

SECRET = "test-only-secret"


@pytest.fixture(autouse=True)
def _no_ci_step_summary(monkeypatch):
    """Tests call evals.run.main(); keep them from writing into the real CI job summary."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.fixture
def secret() -> str:
    return SECRET


@pytest.fixture
def store():
    s = MetadataStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def seeded(store):
    """(store, tenants) with 5 synthetic tenants, each with a unique canary."""
    tenants = seed_demo(store, n_tenants=5, seed=7)
    return store, tenants


@pytest.fixture
def pg_dsns():
    """Admin and app DSNs for Postgres. Skips when unavailable; fails if METAFORGE_REQUIRE_PG=1."""
    import os

    from rag.pg_index import discover_dsns

    dsns = discover_dsns()
    if dsns is None:
        if os.environ.get("METAFORGE_REQUIRE_PG") == "1":
            pytest.fail("METAFORGE_REQUIRE_PG=1 but Postgres is not reachable")
        pytest.skip("Postgres not running (make pg-up)")
    return dsns


def first_doc_llm():
    """FakeLLM that answers with the first document in its prompt and cites it."""
    import json
    import re

    from core.llm import FakeLLM

    pattern = re.compile(r'<document id="([^"]+)"[^>]*>\n(.*?)\n</document>', re.S)

    def script(messages):
        m = pattern.search(messages[0].content)
        if not m:
            return json.dumps({"answer": "none", "citations": []})
        return json.dumps({"answer": m.group(2)[:500], "citations": [m.group(1)]})

    return FakeLLM(script)
