from __future__ import annotations

import time
from typing import Any

from mcp.types import CallToolResult

from ... import metrics as nomad_metrics
from ...otel import get_tracer, set_span_error, set_span_ok
from .base import Middleware, ToolCallContext

_SPAN_KEY = "otel_span"
_START_KEY = "otel_start_time"


class TelemetryMiddleware(Middleware):
    """OpenTelemetry spans around upstream tool invocations."""

    def __init__(self, tracer: Any | None = None):
        self._tracer = tracer or get_tracer("nomad.gateway")

    async def before_tool(self, ctx: ToolCallContext) -> ToolCallContext:
        argument_keys = sorted(str(key) for key in ctx.arguments)
        span = self._tracer.start_span(
            "nomad.gateway.upstream_tool",
            attributes={
                "nomad.gateway.server": ctx.server,
                "nomad.gateway.tool": ctx.tool,
                "nomad.gateway.run_id": ctx.run_id,
                "nomad.gateway.argument_keys": argument_keys,
            },
        )
        ctx.metadata[_SPAN_KEY] = span
        ctx.metadata[_START_KEY] = time.monotonic()
        return ctx

    async def after_tool(
        self,
        ctx: ToolCallContext,
        result: CallToolResult,
    ) -> CallToolResult:
        span = ctx.metadata.pop(_SPAN_KEY, None)
        start_time = ctx.metadata.pop(_START_KEY, None)
        if start_time is not None:
            nomad_metrics.record_gateway_upstream_tool_call(
                ctx.server,
                ctx.tool,
                time.monotonic() - start_time,
                status="ok",
            )
        if span is None:
            return result
        payload = getattr(result, "structuredContent", None)
        span.set_attribute("nomad.gateway.status", "ok")
        span.set_attribute("nomad.gateway.output_present", payload is not None)
        set_span_ok(span)
        span.end()
        return result

    async def on_tool_error(
        self,
        ctx: ToolCallContext,
        exc: Exception,
    ) -> None:
        span: Any = ctx.metadata.pop(_SPAN_KEY, None)
        start_time = ctx.metadata.pop(_START_KEY, None)
        if start_time is not None:
            nomad_metrics.record_gateway_upstream_tool_call(
                ctx.server,
                ctx.tool,
                time.monotonic() - start_time,
                status="error",
            )
        if span is not None:
            span.set_attribute("nomad.gateway.status", "error")
            span.set_attribute("error.type", exc.__class__.__name__)
            set_span_error(span, exc)
            span.end()
        raise exc
