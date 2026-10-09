"""Stage 6: Ask data. Questions about a tenant's records become validated queries, never SQL.

Invariant: a query runs only over the caller's records, only with fields the caller's effective
schema exposes, and restricted fields only for managers, in every clause.
"""

from dataqa.ask import AskResult, ask_data, render_schema, summarize
from dataqa.query import (
    MANAGER_ROLE,
    NAIVE_ENV,
    NAIVE_VALUE,
    Query,
    QueryError,
    QueryResult,
    describe,
    execute,
    naive_dataqa_enabled,
    parse_query,
    schema_for,
    validate,
)

__all__ = [
    "MANAGER_ROLE",
    "NAIVE_ENV",
    "NAIVE_VALUE",
    "AskResult",
    "Query",
    "QueryError",
    "QueryResult",
    "ask_data",
    "describe",
    "execute",
    "naive_dataqa_enabled",
    "parse_query",
    "render_schema",
    "schema_for",
    "summarize",
    "validate",
]
