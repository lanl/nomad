from __future__ import annotations

import fnmatch
from collections.abc import Sequence

from .base import Middleware, ToolCallContext


class AllowlistMiddleware(Middleware):
    """Restrict tool usage to an allowlist/denylist of patterns."""

    def __init__(
        self,
        allow: Sequence[str] | None = None,
        deny: Sequence[str] | None = None,
    ):
        self._allow = list(allow or [])
        self._deny = list(deny or [])

    def _match(self, patterns: Sequence[str], value: str) -> bool:
        return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)

    async def before_tool(self, ctx: ToolCallContext) -> ToolCallContext:
        label = f"{ctx.server}:{ctx.tool}"
        if self._deny and self._match(self._deny, label):
            raise PermissionError(f"Tool '{label}' denied by allowlist policy")
        if self._allow and not self._match(self._allow, label):
            raise PermissionError(f"Tool '{label}' not allowed by policy")
        return ctx
