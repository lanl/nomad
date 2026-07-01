from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from mcp.types import CallToolResult


@dataclass(slots=True)
class ToolCallContext:
    """Metadata describing a tool invocation."""

    server: str
    tool: str
    arguments: dict[str, Any]
    run_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Middleware:
    """Base class for middleware plugins."""

    async def before_tool(self, ctx: ToolCallContext) -> ToolCallContext:  # noqa: D401
        """Hook executed before forwarding the tool call."""
        return ctx

    async def after_tool(
        self,
        ctx: ToolCallContext,
        result: CallToolResult,
    ) -> CallToolResult:
        """Hook executed after a successful tool call."""
        return result

    async def on_tool_error(
        self,
        ctx: ToolCallContext,
        exc: Exception,
    ) -> None:
        """Hook executed when the tool raises an exception."""
        raise exc


class MiddlewareChain:
    """Compose middleware for upstream tool invocations."""

    def __init__(self, middleware: Sequence[Middleware]):
        self._middleware = list(middleware)

    def extend(self, additional: Sequence[Middleware]) -> MiddlewareChain:
        return MiddlewareChain([*self._middleware, *additional])

    async def before_tool(self, ctx: ToolCallContext) -> ToolCallContext:
        for mw in self._middleware:
            ctx = await mw.before_tool(ctx)
        return ctx

    async def after_tool(
        self,
        ctx: ToolCallContext,
        result: CallToolResult,
    ) -> CallToolResult:
        for mw in reversed(self._middleware):
            result = await mw.after_tool(ctx, result)
        return result

    async def on_error(self, ctx: ToolCallContext, exc: Exception) -> None:
        errors = []
        for mw in reversed(self._middleware):
            try:
                await mw.on_tool_error(ctx, exc)
            except Exception as err:  # noqa: BLE001
                errors.append(err)
        if errors:
            raise errors[-1]
        raise exc

    @contextlib.asynccontextmanager
    async def around_tool(
        self,
        ctx: ToolCallContext,
    ) -> AsyncIterator[ToolCallContext]:
        ctx = await self.before_tool(ctx)
        try:
            yield ctx
        except Exception as exc:  # noqa: BLE001
            await self.on_error(ctx, exc)
