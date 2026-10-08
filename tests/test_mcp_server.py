"""Stage 2 MCP adapter, exercised through a real in-process MCP client (both protocol eras)."""

import anyio
import pytest

pytest.importorskip("mcp")

import mcp_types as types  # noqa: E402
from mcp import Client  # noqa: E402

from mcp_server.server import build_server  # noqa: E402
from mcp_server.tools import TenantTools  # noqa: E402

VEHICLE = {
    "action": "create_object",
    "object": {"name": "vehicle", "fields": [{"name": "plate", "type": "string"}]},
}

MODES = ["auto", "legacy"]  # auto negotiates 2026-07-28 (input_required); legacy uses elicitation


@pytest.fixture
def env(seeded):
    store, tenants = seeded
    tools = TenantTools(store)
    return store, tools, tenants[0], build_server(tools, tenants[0].context())


def run(server, body, **kw):
    async def main():
        async with Client(server, **kw) as client:
            return await body(client)

    return anyio.run(main)


async def accept(context, params):
    return types.ElicitResult(action="accept", content={"confirm": True})


def test_tools_are_listed_with_annotations(env):
    *_, server = env

    async def body(client):
        return {t.name: t for t in (await client.list_tools()).tools}

    tools = run(server, body)
    assert set(tools) == {"list_objects", "describe_object", "propose_change", "apply_change"}
    assert tools["list_objects"].annotations.read_only_hint is True
    assert tools["apply_change"].annotations.destructive_hint is True
    for tool in tools.values():
        props = tool.input_schema.get("properties", {})
        assert not set(props) & {"tenant_id", "tenant", "tid", "tenantId"}


def test_tenant_comes_from_the_session_not_the_arguments(env):
    _, _, me, server = env

    async def body(client):
        return await client.call_tool("list_objects", {"tenant_id": "cinder"})

    result = run(server, body)
    assert not result.is_error
    names = [o["name"] for o in result.structured_content["objects"]]
    assert f"{me.tenant_id}_asset" in names and "cinder_asset" not in names


def test_errors_are_returned_not_raised(env):
    *_, server = env

    async def body(client):
        return await client.call_tool("describe_object", {"name": "zz_missing"})

    result = run(server, body)
    assert result.is_error and "no object" in result.content[0].text


@pytest.mark.parametrize("mode", MODES)
def test_confirmed_apply_writes(env, mode):
    store, _, me, server = env
    asked = []

    async def confirm(context, params):
        asked.append(params.message)
        return await accept(context, params)

    async def body(client):
        proposed = await client.call_tool("propose_change", {"proposal": VEHICLE})
        change_id = proposed.structured_content["change_id"]
        return await client.call_tool("apply_change", {"change_id": change_id})

    result = run(server, body, elicitation_callback=confirm, mode=mode)
    assert not result.is_error, result.content
    assert store.get_object(me.context(), "vehicle") is not None
    assert asked and "vehicle" in asked[0]  # the human saw what they were approving


@pytest.mark.parametrize("mode", MODES)
def test_declined_apply_does_not_write(env, mode):
    store, _, me, server = env

    async def decline(context, params):
        return types.ElicitResult(action="decline")

    async def body(client):
        proposed = await client.call_tool("propose_change", {"proposal": VEHICLE})
        return await client.call_tool(
            "apply_change", {"change_id": proposed.structured_content["change_id"]}
        )

    result = run(server, body, elicitation_callback=decline, mode=mode)
    assert result.is_error
    assert store.get_object(me.context(), "vehicle") is None


@pytest.mark.parametrize("mode", MODES)
def test_no_elicitation_support_means_no_write(env, mode):
    store, _, me, server = env

    async def body(client):
        proposed = await client.call_tool("propose_change", {"proposal": VEHICLE})
        try:
            return await client.call_tool(
                "apply_change", {"change_id": proposed.structured_content["change_id"]}
            )
        except Exception as exc:  # noqa: BLE001 - client-side refusal is fine
            return exc

    run(server, body, mode=mode)
    assert store.get_object(me.context(), "vehicle") is None


def test_apply_of_unknown_change_does_not_prompt(env):
    *_, server = env
    asked = []

    async def confirm(context, params):
        asked.append(params.message)
        return await accept(context, params)

    async def body(client):
        return await client.call_tool("apply_change", {"change_id": "nope"})

    result = run(server, body, elicitation_callback=confirm)
    assert result.is_error and asked == []


def test_stdio_server_end_to_end(tmp_path, secret):
    """Launch `python -m mcp_server` as a subprocess, the way an MCP client does."""
    import os
    import sys

    from mcp import StdioServerParameters

    from core.auth import issue_token
    from core.store import MetadataStore
    from core.synthetic import seed_demo

    db = tmp_path / "stdio.db"
    store = MetadataStore(str(db))
    tenants = seed_demo(store)
    store.close()
    me, other = tenants[0], tenants[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server"],
        env={
            "METAFORGE_AUTH_SECRET": secret,
            "METAFORGE_DB": str(db),
            "METAFORGE_TOKEN": issue_token(secret, me.tenant_id),
            "PATH": os.environ.get("PATH", ""),
        },
    )

    async def body(client):
        return await client.call_tool("list_objects", {"tenant_id": other.tenant_id})

    result = run(params, body)
    names = [o["name"] for o in result.structured_content["objects"]]
    assert f"{me.tenant_id}_asset" in names and f"{other.tenant_id}_asset" not in names
