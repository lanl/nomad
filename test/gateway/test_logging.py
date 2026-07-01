from __future__ import annotations

import json
import logging

import pytest
from fastmcp.server.context import to_client_logger

from nomad.gateway.middleware.base import ToolCallContext
from nomad.gateway.middleware.logging import LoggingMiddleware
from nomad.logging_utils import build_jsonl_file_handler, configure_root_logging


@pytest.mark.asyncio
async def test_logging_middleware_excludes_arguments_by_default(caplog):
    middleware = LoggingMiddleware()
    ctx = ToolCallContext(
        server="dummy",
        tool="alpha",
        arguments={"value": 1},
        run_id="run-1",
    )
    caplog.set_level(logging.INFO, logger="nomad.gateway.tools")

    await middleware.before_tool(ctx)

    record = caplog.records[-1]
    assert not hasattr(record, "arguments")


@pytest.mark.asyncio
async def test_logging_middleware_includes_arguments_when_enabled(caplog):
    middleware = LoggingMiddleware(include_args=True)
    ctx = ToolCallContext(
        server="dummy",
        tool="alpha",
        arguments={"value": 2},
        run_id="run-2",
    )
    caplog.set_level(logging.INFO, logger="nomad.gateway.tools")

    await middleware.before_tool(ctx)

    record = caplog.records[-1]
    assert record.arguments == {"value": 2}


def test_jsonl_handler_captures_fastmcp_to_client_logs(tmp_path):
    log_file = tmp_path / "gateway.jsonl"
    file_handler = build_jsonl_file_handler(log_file, level=logging.DEBUG)
    fastmcp_logger = logging.getLogger("fastmcp")
    original_handlers = fastmcp_logger.handlers[:]
    original_level = fastmcp_logger.level
    original_propagate = fastmcp_logger.propagate
    original_child_level = to_client_logger.level

    fastmcp_logger.handlers.clear()
    fastmcp_logger.propagate = False
    fastmcp_logger.setLevel(logging.DEBUG)
    fastmcp_logger.addHandler(file_handler)
    to_client_logger.setLevel(logging.INFO)

    try:
        to_client_logger.info(
            "Sending INFO to client: execute_mcp_code started",
            extra={"request_id": "req-123", "phase": "start"},
        )
    finally:
        fastmcp_logger.removeHandler(file_handler)
        file_handler.close()
        fastmcp_logger.handlers[:] = original_handlers
        fastmcp_logger.setLevel(original_level)
        fastmcp_logger.propagate = original_propagate
        to_client_logger.setLevel(original_child_level)

    payload = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert payload["logger"] == "fastmcp.server.context.to_client"
    assert payload["message"] == "Sending INFO to client: execute_mcp_code started"
    assert payload["request_id"] == "req-123"
    assert payload["phase"] == "start"


def test_configure_root_logging_quiets_dependency_stderr(capsys):
    file_handler = configure_root_logging(
        stderr_level=logging.INFO,
        format_string="{message}",
    )

    try:
        logging.getLogger("nomad").info("nomad marker")
        logging.getLogger("httpx").info("HTTP Request: GET https://example.test")
        logging.getLogger("httpcore").info("connect_tcp.started")
    finally:
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        if file_handler is not None:
            file_handler.close()

    captured = capsys.readouterr()
    assert "nomad marker" in captured.err
    assert "HTTP Request" not in captured.err
    assert "connect_tcp.started" not in captured.err
