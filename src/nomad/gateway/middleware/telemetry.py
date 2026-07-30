from __future__ import annotations

import time

from mcp.types import CallToolResult

from ... import metrics as nomad_metrics
from .base import Middleware, ToolCallContext

_START_KEY = "otel_start_time"


class TelemetryMiddleware(Middleware):
    """Nomad metrics around FastMCP-instrumented upstream tool calls."""

    async def before_tool(self, ctx: ToolCallContext) -> ToolCallContext:
        ctx.metadata[_START_KEY] = time.monotonic()
        return ctx

    async def after_tool(
        self,
        ctx: ToolCallContext,
        result: CallToolResult,
    ) -> CallToolResult:
        start_time = ctx.metadata.pop(_START_KEY, None)
        if start_time is not None:
            nomad_metrics.record_gateway_upstream_tool_call(
                ctx.server,
                ctx.tool,
                time.monotonic() - start_time,
                status="ok",
            )
        return result

    async def on_tool_error(
        self,
        ctx: ToolCallContext,
        exc: Exception,
    ) -> None:
        start_time = ctx.metadata.pop(_START_KEY, None)
        if start_time is not None:
            nomad_metrics.record_gateway_upstream_tool_call(
                ctx.server,
                ctx.tool,
                time.monotonic() - start_time,
                status="error",
            )
        raise exc
