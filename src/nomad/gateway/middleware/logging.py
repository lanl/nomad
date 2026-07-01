from __future__ import annotations

import logging
import time

from mcp.types import CallToolResult

from .base import Middleware, ToolCallContext


class LoggingMiddleware(Middleware):
    """Structured logging around tool invocations."""

    def __init__(self, level: str = "info", *, include_args: bool = False):
        self._logger = logging.getLogger("nomad.gateway.tools")
        self._level = getattr(logging, level.upper(), logging.INFO)
        self._include_args = include_args

    async def before_tool(self, ctx: ToolCallContext) -> ToolCallContext:
        ctx.metadata["start_ns"] = time.perf_counter_ns()
        extra = {
            "server": ctx.server,
            "tool": ctx.tool,
            "run_id": ctx.run_id,
        }
        if self._include_args:
            extra["arguments"] = ctx.arguments
        self._logger.log(
            self._level,
            "Calling upstream tool",
            extra=extra,
        )
        return ctx

    async def after_tool(
        self,
        ctx: ToolCallContext,
        result: CallToolResult,
    ) -> CallToolResult:
        start_ns = ctx.metadata.pop("start_ns", None)
        duration_ms = (time.perf_counter_ns() - start_ns) / 1e6 if start_ns else None
        payload = getattr(result, "structuredContent", None)
        size = 0
        if payload is not None:
            try:
                size = len(str(payload))
            except Exception:  # noqa: BLE001
                size = 0
        self._logger.log(
            self._level,
            "Upstream tool completed",
            extra={
                "server": ctx.server,
                "tool": ctx.tool,
                "duration_ms": duration_ms,
                "bytes_out": size,
                "run_id": ctx.run_id,
            },
        )
        return result

    async def on_tool_error(
        self,
        ctx: ToolCallContext,
        exc: Exception,
    ) -> None:
        start_ns = ctx.metadata.pop("start_ns", None)
        duration_ms = (time.perf_counter_ns() - start_ns) / 1e6 if start_ns else None
        self._logger.error(
            "Upstream tool error",
            exc_info=exc,
            extra={
                "server": ctx.server,
                "tool": ctx.tool,
                "duration_ms": duration_ms,
                "run_id": ctx.run_id,
            },
        )
        raise exc
