"""Stage 2: tenant-scoped MCP tools over the metadata store.

Invariant: the tenant is fixed per server session from a verified token. Model-supplied tenant
arguments are ignored and audited, and every write needs a human confirmation bound to that change.
`tools.py` holds the logic with no SDK dependency; `server.py` adapts it to MCP.
"""

from mcp_server.tools import (
    TENANT_ARG_KEYS,
    AuditEvent,
    Confirmation,
    TenantTools,
    ToolError,
    naive_mode_from_env,
)

__all__ = [
    "TENANT_ARG_KEYS",
    "AuditEvent",
    "Confirmation",
    "TenantTools",
    "ToolError",
    "naive_mode_from_env",
]
