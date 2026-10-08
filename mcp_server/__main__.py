"""Run the MCP server over stdio for one tenant.

    METAFORGE_AUTH_SECRET=... METAFORGE_TOKEN=$(python -m core.cli token fjord) python -m mcp_server

The tenant comes only from the verified token. Logs go to stderr; stdout is the MCP channel.
"""

from __future__ import annotations

import logging
import os
import sys

import anyio
from mcp.server.stdio import stdio_server

from core.auth import AuthError, verify_token
from core.config import Settings
from core.store.sqlite_store import MetadataStore
from mcp_server.server import build_server
from mcp_server.tools import TenantTools, naive_mode_from_env


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    settings = Settings.from_env()
    token = os.environ.get("METAFORGE_TOKEN", "")
    try:
        ctx = verify_token(settings.auth_secret, token)
    except AuthError as exc:
        print(f"METAFORGE_TOKEN is missing or invalid: {exc}", file=sys.stderr)
        return 2

    store = MetadataStore(settings.db_path)
    tools = TenantTools(store, naive=naive_mode_from_env())
    server = build_server(tools, ctx)
    logging.getLogger("metaforge.mcp").info(
        "serving tenant=%s db=%s", ctx.tenant_id, settings.db_path
    )

    async def serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    try:
        anyio.run(serve)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
