from __future__ import annotations

import re
from typing import Any, Literal

from mcp.types import CallToolResult

from .base import Middleware, ToolCallContext

SECRET_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*([A-Za-z0-9_\-]{6,})"
    ),
    re.compile(r"[A-Za-z0-9+/]{32,}={0,2}"),
]


def redact_text(text: str) -> str:
    """Coarse heuristic to redact obvious secrets."""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(r"\1=<redacted>", redacted)
    return redacted


def _redact(value: Any, mode: Literal["basic", "none"]) -> Any:
    if mode == "none":
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact(item, mode) for item in value]
    if isinstance(value, dict):
        return {key: _redact(val, mode) for key, val in value.items()}
    return value


class RedactionMiddleware(Middleware):
    """Best-effort redaction of sensitive data in tool responses."""

    def __init__(self, mode: Literal["basic", "none"] = "basic"):
        self._mode = mode

    async def after_tool(
        self,
        ctx: ToolCallContext,
        result: CallToolResult,
    ) -> CallToolResult:
        if self._mode == "none":
            return result
        payload = result.model_dump(mode="python")
        sanitized = _redact(payload, self._mode)
        return CallToolResult.model_validate(sanitized)
