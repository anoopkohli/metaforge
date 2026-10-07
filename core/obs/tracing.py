"""Tracing helper.

Uses OpenTelemetry when installed (`pip install -e ".[otel]"`) and is a silent no-op otherwise.
Every span is tagged with the active tenant (if any), so stage 5's triage agent can ask
"which tenant is this latency coming from?" from trace data alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from core.tenancy.context import current_tenant

try:  # pragma: no cover - exercised only when the optional extra is installed
    from opentelemetry import trace as _trace

    _tracer = _trace.get_tracer("metaforge")
except ImportError:
    _trace = None
    _tracer = None


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    ctx = current_tenant()
    attrs = dict(attributes)
    if ctx is not None:
        attrs.setdefault("tenant.id", ctx.tenant_id)
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name, attributes=attrs) as s:  # pragma: no cover
        yield s
