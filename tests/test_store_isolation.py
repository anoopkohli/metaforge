"""Tenant isolation tests for the metadata store (the stage-0 version of the attack suite).

A leak is detected objectively: each seeded tenant has a unique canary string in its own data.
If any call made as tenant A returns data containing tenant B's canary, isolation is broken.
"""

import pytest

from core.store import FieldDef, ObjectDef, ObjectNameConflict, ObjectNotFound, ObjectOverride
from core.tenancy import MissingTenantContext, TenantContext


def _obj(name: str, label: str | None = None) -> ObjectDef:
    return ObjectDef(name=name, label=label, fields=[FieldDef(name="tag", type="string")])


# ---- the canary sweep -------------------------------------------------------------------------


def _everything_visible_to(store, ctx) -> str:
    """Serialise every read path available to a tenant into one string."""
    parts: list[str] = []
    for name in store.list_objects(ctx):
        parts.append(name)
        eff = store.get_effective_schema(ctx, name)
        parts.append(eff.model_dump_json() if eff else "")
        custom = store.get_object(ctx, name)
        parts.append(custom.model_dump_json() if custom else "")
        override = store.get_override(ctx, name)
        parts.append(override.model_dump_json() if override else "")
    return "\n".join(parts)


def test_no_tenant_sees_another_tenants_canary(seeded):
    store, tenants = seeded
    assert len({t.canary for t in tenants}) == len(tenants)  # canaries are unique
    for me in tenants:
        visible = _everything_visible_to(store, me.context())
        assert me.canary in visible  # sanity: the sweep does see my own data
        for other in tenants:
            if other.tenant_id != me.tenant_id:
                assert other.canary not in visible, f"{me.tenant_id} saw {other.tenant_id}'s canary"
                assert other.tenant_id + "_asset" not in visible


def test_other_tenants_custom_object_is_invisible_by_name(seeded):
    store, tenants = seeded
    a, b = tenants[0], tenants[1]
    assert store.get_object(a.context(), f"{b.tenant_id}_asset") is None
    assert store.get_effective_schema(a.context(), f"{b.tenant_id}_asset") is None
    assert f"{b.tenant_id}_asset" not in store.list_objects(a.context())


# ---- same names do not collide ----------------------------------------------------------------


def test_same_object_name_in_two_tenants_is_independent(store):
    a, b = TenantContext("acme"), TenantContext("borealis")
    store.put_object(a, _obj("invoice", "Acme Invoice"))
    store.put_object(b, _obj("invoice", "Borealis Invoice"))
    assert store.get_object(a, "invoice").label == "Acme Invoice"
    assert store.get_object(b, "invoice").label == "Borealis Invoice"


def test_delete_only_affects_own_tenant(store):
    a, b = TenantContext("acme"), TenantContext("borealis")
    store.put_object(a, _obj("invoice"))
    store.put_object(b, _obj("invoice"))
    assert store.delete_object(a, "invoice") is True
    assert store.get_object(a, "invoice") is None
    assert store.get_object(b, "invoice") is not None
    assert store.delete_object(a, "invoice") is False  # already gone


def test_update_bumps_version_per_tenant(store):
    a, b = TenantContext("acme"), TenantContext("borealis")
    assert store.put_object(a, _obj("invoice")) == 1
    assert store.put_object(a, _obj("invoice")) == 2
    assert store.put_object(b, _obj("invoice")) == 1  # b's counter is independent


# ---- hostile inputs ---------------------------------------------------------------------------


@pytest.mark.parametrize("evil", ["x' OR '1'='1", "x'; DROP TABLE tenant_objects;--", "%", "*"])
def test_sql_metacharacters_in_object_name_do_not_widen_the_query(seeded, evil):
    store, tenants = seeded
    ctx = tenants[0].context()
    assert store.get_object(ctx, evil) is None
    assert store.get_effective_schema(ctx, evil) is None
    # and the data is all still there
    assert f"{tenants[1].tenant_id}_asset" not in store.list_objects(ctx)
    assert f"{tenants[0].tenant_id}_asset" in store.list_objects(ctx)


def test_tenant_id_cannot_be_smuggled_in_via_a_forged_context(store):
    # TenantContext validates IDs, so a wildcard or injected ID cannot even be constructed.
    for evil in ["%", "a' OR '1'='1", "*"]:
        with pytest.raises(ValueError):
            TenantContext(evil)


# ---- no context, no access --------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.list_objects(None),
        lambda s: s.get_object(None, "worker"),
        lambda s: s.get_effective_schema(None, "worker"),
        lambda s: s.get_override(None, "worker"),
        lambda s: s.put_object(None, _obj("x")),
        lambda s: s.delete_object(None, "x"),
        lambda s: s.put_override(None, "worker", ObjectOverride()),
    ],
)
def test_every_tenant_method_refuses_a_missing_context(seeded, call):
    store, _ = seeded
    with pytest.raises(MissingTenantContext):
        call(store)


def test_a_string_is_not_a_context(seeded):
    store, tenants = seeded
    with pytest.raises(TypeError):
        store.list_objects(tenants[0].tenant_id)  # type: ignore[arg-type]


# ---- overrides and effective schema -----------------------------------------------------------


def test_override_applies_only_to_the_tenant_that_set_it(store):
    from core.synthetic import base_catalog

    for obj in base_catalog():
        store.platform_put_base_object(obj)
    a, b = TenantContext("acme"), TenantContext("borealis")
    store.put_override(a, "worker", ObjectOverride(hide_fields=["notes"]))

    a_fields = {f.name for f in store.get_effective_schema(a, "worker").fields}
    b_fields = {f.name for f in store.get_effective_schema(b, "worker").fields}
    assert "notes" not in a_fields
    assert "notes" in b_fields


def test_override_cannot_hide_required_or_unknown_fields(store):
    from core.synthetic import base_catalog

    for obj in base_catalog():
        store.platform_put_base_object(obj)
    a = TenantContext("acme")
    with pytest.raises(ValueError):
        store.put_override(a, "worker", ObjectOverride(hide_fields=["full_name"]))  # required
    with pytest.raises(ValueError):
        store.put_override(a, "worker", ObjectOverride(hide_fields=["nope"]))
    with pytest.raises(ValueError):
        store.put_override(
            a, "worker", ObjectOverride(add_fields=[FieldDef(name="notes", type="string")])
        )
    assert store.get_override(a, "worker") is None  # nothing invalid was stored


def test_override_on_unknown_base_object(store):
    with pytest.raises(ObjectNotFound):
        store.put_override(TenantContext("acme"), "ghost", ObjectOverride())


def test_custom_object_cannot_shadow_a_platform_object(store):
    from core.synthetic import base_catalog

    for obj in base_catalog():
        store.platform_put_base_object(obj)
    with pytest.raises(ObjectNameConflict):
        store.put_object(TenantContext("acme"), _obj("worker"))
