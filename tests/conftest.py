import pytest

from core.store.sqlite_store import MetadataStore
from core.synthetic import seed_demo

SECRET = "test-only-secret"


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
