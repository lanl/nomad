from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from nomad.gateway.runtime.runner import _default_serializer

BRIDGE_HEADER_SIZE = 4
BRIDGE_MAX_MESSAGE_SIZE = 16 * 1024 * 1024


def encode_bridge_message(payload: Mapping[str, Any]) -> bytes:
    """Encode a JSON payload as a length-prefixed bridge frame."""
    body = json.dumps(payload, default=_default_serializer).encode("utf-8")
    if len(body) > BRIDGE_MAX_MESSAGE_SIZE:
        raise ValueError(
            f"Bridge message exceeds max size ({BRIDGE_MAX_MESSAGE_SIZE} bytes)"
        )
    return len(body).to_bytes(BRIDGE_HEADER_SIZE, byteorder="big") + body


async def read_bridge_message(
    reader: asyncio.StreamReader,
) -> dict[str, Any] | None:
    """Read one length-prefixed JSON bridge frame from ``reader``."""
    try:
        header = await reader.readexactly(BRIDGE_HEADER_SIZE)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise ConnectionError("Bridge closed connection mid-frame") from exc
    message_size = int.from_bytes(header, byteorder="big")
    if message_size > BRIDGE_MAX_MESSAGE_SIZE:
        raise ValueError(
            f"Bridge message exceeds max size ({BRIDGE_MAX_MESSAGE_SIZE} bytes)"
        )
    body = await reader.readexactly(message_size)
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Bridge message must decode to a JSON object")
    return payload
