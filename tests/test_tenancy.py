import asyncio

import pytest

from core.tenancy import (
    MissingTenantContext,
    TenantContext,
    current_tenant,
    require_tenant,
    tenant_scope,
)


def test_no_context_is_a_hard_error():
    assert current_tenant() is None
    with pytest.raises(MissingTenantContext):
        require_tenant()


def test_scope_sets_and_restores():
    with tenant_scope(TenantContext("acme")) as ctx:
        assert require_tenant() is ctx
    assert current_tenant() is None


def test_nested_scopes_restore_outer():
    with tenant_scope(TenantContext("acme")):
        with tenant_scope(TenantContext("borealis")):
            assert require_tenant().tenant_id == "borealis"
        assert require_tenant().tenant_id == "acme"


def test_scope_restored_after_exception():
    with pytest.raises(RuntimeError):
        with tenant_scope(TenantContext("acme")):
            raise RuntimeError("boom")
    assert current_tenant() is None


@pytest.mark.parametrize("bad", ["", "A", "1abc", "has space", "x" * 80, "a;drop", "../etc"])
def test_invalid_tenant_ids_rejected(bad):
    with pytest.raises(ValueError):
        TenantContext(bad)


def test_scope_requires_a_real_context():
    with pytest.raises(TypeError):
        with tenant_scope("acme"):  # type: ignore[arg-type]
            pass


def test_concurrent_tasks_do_not_see_each_others_tenant():
    async def worker(tid: str, results: dict):
        with tenant_scope(TenantContext(tid)):
            await asyncio.sleep(0.01)  # yield so the tasks interleave
            results[tid] = require_tenant().tenant_id

    async def main():
        results: dict[str, str] = {}
        await asyncio.gather(*(worker(t, results) for t in ["acme", "borealis", "cinder"]))
        return results

    results = asyncio.run(main())
    assert results == {"acme": "acme", "borealis": "borealis", "cinder": "cinder"}
