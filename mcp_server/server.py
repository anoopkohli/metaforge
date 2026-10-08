"""MCP adapter over `TenantTools`, using the low-level SDK server so raw arguments reach our code.

Invariants:
- One server instance serves one `TenantContext`, fixed when it is built (from a verified token).
- `apply_change` gets a `Confirmation` only after the client returns an accepted confirmation for
  that change: by form elicitation (2025-11-25), or by an `input_required` round trip (2026-07-28)
  whose `request_state` is HMAC-signed over tenant, change ID and expiry. Else nothing is written.

Run over stdio: `METAFORGE_TOKEN=... python -m mcp_server` (see README for client setup).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any

import mcp_types as types
from mcp.server import Server
from mcp.shared.exceptions import NoBackChannelError

from core.tenancy.context import TenantContext, tenant_scope
from mcp_server.tools import Confirmation, TenantTools, ToolError

log = logging.getLogger("metaforge.mcp")

CONFIRM_KEY = "confirm"
CONFIRM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"confirm": {"type": "boolean", "title": "Apply this change?"}},
    "required": ["confirm"],
}


class StateSigner:
    """Signs the opaque `request_state` for the 2026-07-28 confirmation round trip."""

    def __init__(
        self, key: bytes | None = None, ttl_s: float = 300.0, clock: Callable[[], float] = time.time
    ) -> None:
        self._key = key or secrets.token_bytes(32)
        self._ttl_s = ttl_s
        self._clock = clock

    def _mac(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def sign(self, tenant_id: str, change_id: str) -> str:
        expires = int(self._clock() + self._ttl_s)
        payload = f"{tenant_id}|{change_id}|{expires}|{secrets.token_urlsafe(8)}"
        return f"{payload}.{self._mac(payload)}"

    def verify(self, state: str | None, tenant_id: str, change_id: str) -> bool:
        if not state or "." not in state:
            return False
        payload, mac = state.rsplit(".", 1)
        if not hmac.compare_digest(self._mac(payload), mac):
            return False
        try:
            s_tenant, s_change, s_expires, _ = payload.split("|")
            expires = int(s_expires)
        except ValueError:
            return False
        return s_tenant == tenant_id and s_change == change_id and self._clock() < expires


def _ok(result: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result))],
        structured_content=result,
    )


def _err(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )


def _accepted(response: Any) -> bool:
    return (
        isinstance(response, types.ElicitResult)
        and response.action == "accept"
        and isinstance(response.content, dict)
        and response.content.get(CONFIRM_KEY) is True
    )


def build_server(
    tools: TenantTools, ctx: TenantContext, *, signer: StateSigner | None = None
) -> Server:
    if not isinstance(ctx, TenantContext):
        raise TypeError("build_server needs a TenantContext")
    signer = signer or StateSigner()

    listed = [
        types.Tool(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema,
            annotations=types.ToolAnnotations(
                read_only_hint=spec.read_only,
                destructive_hint=spec.destructive,
                open_world_hint=False,
            ),
        )
        for spec in tools.specs
    ]

    async def on_list_tools(rctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=listed)

    async def apply_change(rctx, params) -> types.CallToolResult | types.InputRequiredResult:
        args = params.arguments or {}
        change_id = args.get("change_id")
        summary = tools.pending_summary(ctx, change_id) if isinstance(change_id, str) else None
        if summary is None:
            # Unknown change or bad arguments: let the tool layer raise its normal error. No prompt.
            return _ok(tools.call(ctx, "apply_change", args))

        message = f"Apply this change for tenant '{ctx.tenant_id}'?\n{summary}"
        if params.input_responses is not None or params.request_state is not None:
            if not signer.verify(params.request_state, ctx.tenant_id, change_id):
                return _err("the confirmation could not be verified; nothing was written")
            response = (params.input_responses or {}).get(CONFIRM_KEY)
        else:
            try:
                response = await rctx.session.elicit_form(message, CONFIRM_SCHEMA, rctx.request_id)
            except NoBackChannelError:
                request = types.ElicitRequest(
                    params=types.ElicitRequestFormParams(
                        message=message, requested_schema=CONFIRM_SCHEMA
                    )
                )
                return types.InputRequiredResult(
                    input_requests={CONFIRM_KEY: request},
                    request_state=signer.sign(ctx.tenant_id, change_id),
                )
            except Exception:  # noqa: BLE001 - client cannot ask: fail closed
                log.warning("tenant=%s could not elicit confirmation", ctx.tenant_id, exc_info=True)
                return _err("could not ask the user to confirm; nothing was written")

        if not _accepted(response):
            return _err("the user did not approve the change; nothing was written")
        return _ok(tools.call(ctx, "apply_change", args, confirmation=Confirmation(change_id)))

    async def on_call_tool(rctx, params) -> types.CallToolResult | types.InputRequiredResult:
        with tenant_scope(ctx):
            try:
                if params.name == "apply_change":
                    return await apply_change(rctx, params)
                return _ok(tools.call(ctx, params.name, params.arguments))
            except ToolError as exc:
                return _err(str(exc))
            except Exception:  # noqa: BLE001 - never leak internals to the model
                log.exception("tenant=%s tool=%s failed", ctx.tenant_id, params.name)
                return _err("internal error")

    return Server(
        "metaforge",
        instructions=(
            "Tools over one tenant's metadata. The tenant is fixed by the session; never pass a "
            "tenant. Use propose_change, then apply_change; the user approves every write."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
