"""Attacks on the stage-2 MCP server. Each attack returns True if it *succeeded* (a security failure).

Every attack runs against a fresh seeded store. The attacker is tenant 0 (fjord); the victim is tenant 1
(cinder). Success is judged objectively: the victim's canary or object appears in the attacker's results,
or a write lands without a valid confirmation.

In secure mode every attack must fail. In naive mode (see `mcp_server.tools`) the tenant-argument attacks
must succeed, which proves the suite can tell the difference.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import anyio

from core.store.sqlite_store import MetadataStore
from core.synthetic import SyntheticTenant, seed_demo
from mcp_server.tools import Confirmation, TenantTools, ToolError

VEHICLE = {
    "action": "create_object",
    "object": {"name": "vehicle", "fields": [{"name": "plate", "type": "string"}]},
}


@dataclass
class Env:
    store: MetadataStore
    tools: TenantTools
    attacker: SyntheticTenant
    victim: SyntheticTenant

    def victim_has_vehicle(self) -> bool:
        return self.store.get_object(self.victim.context(), "vehicle") is not None

    def anyone_has_vehicle(self) -> bool:
        return any(
            self.store.get_object(t.context(), "vehicle") is not None
            for t in (self.attacker, self.victim)
        )


def _env(naive: bool) -> Env:
    store = MetadataStore(":memory:")
    tenants = seed_demo(store, n_tenants=5, seed=7)
    return Env(store, TenantTools(store, naive=naive), tenants[0], tenants[1])


def _call(env: Env, ctx_tenant: SyntheticTenant, name: str, args: dict, **kw) -> dict | str:
    """Call a tool; a ToolError is returned as its message (attacks look at both)."""
    try:
        return env.tools.call(ctx_tenant.context(), name, args, **kw)
    except ToolError as exc:
        return str(exc)


def _leaks_victim(env: Env, result: dict | str) -> bool:
    text = json.dumps(result) if isinstance(result, dict) else result
    return env.victim.canary in text or f"{env.victim.tenant_id}_asset" in text


# ---- tool-level attacks -----------------------------------------------------------------------


def tenant_arg_list(env: Env) -> bool:
    result = _call(env, env.attacker, "list_objects", {"tenant_id": env.victim.tenant_id})
    return _leaks_victim(env, result)


def tenant_arg_describe(env: Env) -> bool:
    name = f"{env.victim.tenant_id}_asset"
    result = _call(env, env.attacker, "describe_object", {"name": name, "tenant": env.victim.tenant_id})
    return _leaks_victim(env, result)


def tenant_arg_write(env: Env) -> bool:
    proposed = _call(
        env, env.attacker, "propose_change", {"proposal": VEHICLE, "tenantId": env.victim.tenant_id}
    )
    if not isinstance(proposed, dict) or "change_id" not in proposed:
        return False
    change_id = proposed["change_id"]
    # Even with a genuine human approval, the write must land in the attacker's own tenant.
    _call(
        env,
        env.attacker,
        "apply_change",
        {"change_id": change_id, "tid": env.victim.tenant_id},
        confirmation=Confirmation(change_id),
    )
    return env.victim_has_vehicle()


def hidden_field(env: Env) -> bool:
    result = _call(env, env.attacker, "describe_object", {"name": "worker"})
    return isinstance(result, dict) and any(f["name"] == "notes" for f in result["fields"])


def existence_oracle(env: Env) -> bool:
    foreign = _call(env, env.attacker, "describe_object", {"name": f"{env.victim.tenant_id}_asset"})
    missing = _call(env, env.attacker, "describe_object", {"name": "zz_no_such_object"})
    if isinstance(foreign, dict):
        return True
    normalise = lambda msg, name: msg.replace(repr(name), "<name>")  # noqa: E731
    return normalise(foreign, f"{env.victim.tenant_id}_asset") != normalise(
        missing, "zz_no_such_object"
    )


def apply_without_confirmation(env: Env) -> bool:
    proposed = _call(env, env.attacker, "propose_change", {"proposal": VEHICLE})
    _call(env, env.attacker, "apply_change", {"change_id": proposed["change_id"]})
    return env.anyone_has_vehicle()


def confirmation_for_other_change(env: Env) -> bool:
    first = _call(env, env.attacker, "propose_change", {"proposal": VEHICLE})
    other = {**VEHICLE, "object": {**VEHICLE["object"], "name": "trailer"}}
    second = _call(env, env.attacker, "propose_change", {"proposal": other})
    _call(
        env,
        env.attacker,
        "apply_change",
        {"change_id": first["change_id"]},
        confirmation=Confirmation(second["change_id"]),
    )
    return env.anyone_has_vehicle()


def foreign_change_id(env: Env) -> bool:
    proposed = _call(env, env.victim, "propose_change", {"proposal": VEHICLE})
    change_id = proposed["change_id"]
    _call(
        env, env.attacker, "apply_change", {"change_id": change_id}, confirmation=Confirmation(change_id)
    )
    return env.anyone_has_vehicle()


def replay_change_id(env: Env) -> bool:
    proposed = _call(env, env.attacker, "propose_change", {"proposal": VEHICLE})
    change_id = proposed["change_id"]
    confirm = Confirmation(change_id)
    _call(env, env.attacker, "apply_change", {"change_id": change_id}, confirmation=confirm)
    env.store.delete_object(env.attacker.context(), "vehicle")
    second = _call(env, env.attacker, "apply_change", {"change_id": change_id}, confirmation=confirm)
    return isinstance(second, dict) or env.anyone_has_vehicle()


# ---- protocol-level attacks (real MCP client, in process) -------------------------------------


def _with_client(env: Env, body: Callable, **client_kw) -> None:
    from mcp import Client

    from mcp_server.server import build_server

    server = build_server(env.tools, env.attacker.context())

    async def run() -> None:
        async with Client(server, **client_kw) as client:
            await body(client)

    anyio.run(run)


async def _propose(client) -> str:
    result = await client.call_tool("propose_change", {"proposal": VEHICLE})
    return result.structured_content["change_id"]


def declined_confirmation(env: Env) -> bool:
    import mcp_types as types

    async def decline(context, params):
        return types.ElicitResult(action="decline")

    async def body(client):
        change_id = await _propose(client)
        await client.call_tool("apply_change", {"change_id": change_id})

    _with_client(env, body, elicitation_callback=decline)
    return env.anyone_has_vehicle()


def no_elicitation_support(env: Env) -> bool:
    async def body(client):
        change_id = await _propose(client)
        try:
            await client.call_tool("apply_change", {"change_id": change_id})
        except Exception:  # noqa: BLE001 - the client gives up; what matters is the store
            pass

    _with_client(env, body)
    return env.anyone_has_vehicle()


def _accept():
    import mcp_types as types

    return {"confirm": types.ElicitResult(action="accept", content={"confirm": True})}


def forged_request_state(env: Env) -> bool:
    async def body(client):
        change_id = await _propose(client)
        try:
            await client.call_tool(
                "apply_change",
                {"change_id": change_id},
                input_responses=_accept(),
                request_state="forged-state",
            )
        except Exception:  # noqa: BLE001
            pass

    _with_client(env, body)
    return env.anyone_has_vehicle()


def state_for_other_change(env: Env) -> bool:
    """Get a genuine signed state for change A, then present it with change B."""

    async def body(client):
        a = await _propose(client)
        other = {**VEHICLE, "object": {**VEHICLE["object"], "name": "trailer"}}
        b_result = await client.call_tool("propose_change", {"proposal": other})
        b = b_result.structured_content["change_id"]
        pending = await client.session.call_tool(
            "apply_change", {"change_id": a}, allow_input_required=True
        )
        state = getattr(pending, "request_state", None)
        if state is None:
            return
        try:
            await client.call_tool(
                "apply_change", {"change_id": b}, input_responses=_accept(), request_state=state
            )
        except Exception:  # noqa: BLE001
            pass

    _with_client(env, body)
    return env.store.get_object(env.attacker.context(), "trailer") is not None


ATTACKS: dict[str, Callable[[Env], bool]] = {
    "tenant-arg-list": tenant_arg_list,
    "tenant-arg-describe": tenant_arg_describe,
    "tenant-arg-write": tenant_arg_write,
    "hidden-field": hidden_field,
    "existence-oracle": existence_oracle,
    "apply-without-confirmation": apply_without_confirmation,
    "confirmation-for-other-change": confirmation_for_other_change,
    "foreign-change-id": foreign_change_id,
    "replay-change-id": replay_change_id,
    "declined-confirmation": declined_confirmation,
    "no-elicitation-support": no_elicitation_support,
    "forged-request-state": forged_request_state,
    "state-for-other-change": state_for_other_change,
}

# Attacks that naive mode is *expected* to let through.
NAIVE_EXPECTED_TO_SUCCEED = frozenset({"tenant-arg-list", "tenant-arg-describe", "tenant-arg-write"})


def run_attack(attack_id: str, naive: bool = False) -> bool:
    env = _env(naive)
    try:
        return ATTACKS[attack_id](env)
    finally:
        env.store.close()
