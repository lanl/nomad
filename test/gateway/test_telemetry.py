from __future__ import annotations

from typing import Any

import pytest
from mcp.types import CallToolResult

from nomad import metrics as nomad_metrics
from nomad import otel
from nomad.gateway.config import GatewayConfig, GatewayDefaults
from nomad.gateway.middleware.base import ToolCallContext
from nomad.gateway.middleware.telemetry import TelemetryMiddleware
from nomad.gateway.sandbox import SandboxResult
from nomad.gateway.server import CodeModeGateway


class FakeGatewayContext:
    request_id = "req-1"

    def __init__(self):
        self.messages: list[tuple[str, dict[str, Any] | None]] = []

    async def info(self, message: str, extra: dict[str, Any] | None = None):
        self.messages.append((message, extra))


class FakeProvider:
    def __init__(self):
        self.force_flush_count = 0
        self.shutdown_count = 0

    def force_flush(self):
        self.force_flush_count += 1

    def shutdown(self):
        self.shutdown_count += 1


def _context() -> ToolCallContext:
    return ToolCallContext(
        server="dummy",
        tool="alpha",
        arguments={"value": 1},
        run_id="run-1",
    )


def _gateway_duration_events(monkeypatch):
    events: list[tuple[str, str]] = []

    def capture(kind, _seconds, *, status):
        events.append((kind, status))

    monkeypatch.setattr(nomad_metrics, "record_gateway_request_duration", capture)
    return events


@pytest.mark.asyncio
async def test_telemetry_middleware_records_successful_upstream_metric(monkeypatch):
    events: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        nomad_metrics,
        "record_gateway_upstream_tool_call",
        lambda server, tool, _seconds, *, status: events.append((server, tool, status)),
    )
    middleware = TelemetryMiddleware()
    ctx = _context()

    await middleware.before_tool(ctx)
    await middleware.after_tool(
        ctx,
        CallToolResult(content=[], structured_content={"ok": True}),
    )

    assert events == [("dummy", "alpha", "ok")]


@pytest.mark.asyncio
async def test_telemetry_middleware_records_upstream_error_metric(monkeypatch):
    events: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        nomad_metrics,
        "record_gateway_upstream_tool_call",
        lambda server, tool, _seconds, *, status: events.append((server, tool, status)),
    )
    middleware = TelemetryMiddleware()
    ctx = _context()
    exc = RuntimeError("boom")

    await middleware.before_tool(ctx)
    with pytest.raises(RuntimeError, match="boom"):
        await middleware.on_tool_error(ctx, exc)

    assert events == [("dummy", "alpha", "error")]


def test_gateway_config_accepts_telemetry_section(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "servers: {}",
                "telemetry:",
                "  enabled: true",
                "  service_name: test-nomad",
                "  otlp_endpoint: http://collector:4318",
            ]
        ),
        encoding="utf-8",
    )

    config = GatewayConfig.from_file(config_path)

    assert config.telemetry.enabled is True
    assert config.telemetry.service_name == "test-nomad"
    assert config.telemetry.otlp_endpoint == "http://collector:4318"


def test_otel_env_false_does_not_request_exporter(monkeypatch):
    monkeypatch.setenv("NOMAD_OTEL_ENABLED", "false")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_METRICS_EXPORTER", raising=False)
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)

    assert otel._otel_requested(None) is False


def test_nomad_custom_spans_use_fastmcp_tracer(monkeypatch):
    tracer = object()
    monkeypatch.setattr("fastmcp.telemetry.get_tracer", lambda: tracer)

    assert otel.get_tracer("nomad.gateway") is tracer


def test_shutdown_otel_flushes_without_shutdown(monkeypatch):
    trace_provider = FakeProvider()
    meter_provider = FakeProvider()
    monkeypatch.setattr(otel, "_CONFIGURED_PROVIDER", trace_provider)
    monkeypatch.setattr(otel, "_CONFIGURED_METER_PROVIDER", meter_provider)

    otel.shutdown_otel()

    assert trace_provider.force_flush_count == 1
    assert meter_provider.force_flush_count == 1
    assert trace_provider.shutdown_count == 0
    assert meter_provider.shutdown_count == 0
    assert otel._CONFIGURED_PROVIDER is trace_provider
    assert otel._CONFIGURED_METER_PROVIDER is meter_provider


def test_configure_after_shutdown_keeps_process_global_providers(monkeypatch):
    trace_provider = FakeProvider()
    meter_provider = FakeProvider()
    monkeypatch.setattr(otel, "_CONFIGURED_PROVIDER", trace_provider)
    monkeypatch.setattr(otel, "_CONFIGURED_METER_PROVIDER", meter_provider)

    otel.shutdown_otel()
    configured = otel.configure_otel(
        service_name="nomad-test",
        service_version="0",
        enabled=True,
    )

    assert configured is True
    assert trace_provider.force_flush_count == 1
    assert meter_provider.force_flush_count == 1
    assert trace_provider.shutdown_count == 0
    assert meter_provider.shutdown_count == 0
    assert otel._CONFIGURED_PROVIDER is trace_provider
    assert otel._CONFIGURED_METER_PROVIDER is meter_provider


@pytest.mark.asyncio
async def test_gateway_context_logs_do_not_duplicate_tool_call_telemetry(
    monkeypatch,
    tmp_path,
):
    events = _gateway_duration_events(monkeypatch)
    gateway = CodeModeGateway(
        GatewayConfig(
            defaults=GatewayDefaults(workspace_root=tmp_path / "runs"),
        )
    )
    ctx = FakeGatewayContext()

    async def fake_run_code(code, env=None):
        return SandboxResult(
            stdout="",
            stderr="",
            result={"ok": True},
            returncode=0,
            duration_seconds=0.01,
            tool_calls=[{"server": "dummy", "tool": "alpha"}],
        )

    monkeypatch.setattr(gateway, "run_code", fake_run_code)

    try:
        payload = await gateway.execute_mcp_code("RESULT = 1", ctx)  # type: ignore[arg-type]
    finally:
        await gateway._sandbox.aclose()

    assert payload["result"] == {"ok": True}
    assert "tool_calls" not in payload
    assert events == [("execute_mcp_code", "ok")]
    assert ctx.messages == [
        (
            "execute_mcp_code started",
            {"request_id": "req-1", "phase": "start"},
        ),
        (
            "execute_mcp_code completed",
            {"request_id": "req-1", "phase": "end"},
        ),
    ]


@pytest.mark.asyncio
async def test_gateway_code_timeout_records_duration(monkeypatch, tmp_path):
    events = _gateway_duration_events(monkeypatch)
    gateway = CodeModeGateway(
        GatewayConfig(
            defaults=GatewayDefaults(workspace_root=tmp_path / "runs"),
        )
    )
    ctx = FakeGatewayContext()

    async def fake_run_code(code, env=None):
        raise TimeoutError("slow")

    monkeypatch.setattr(gateway, "run_code", fake_run_code)

    try:
        payload = await gateway.execute_mcp_code("RESULT = 1", ctx)  # type: ignore[arg-type]
    finally:
        await gateway._sandbox.aclose()

    assert payload["result"] == {"error": "timeout"}
    assert events == [("execute_mcp_code", "timeout")]


@pytest.mark.asyncio
async def test_gateway_code_error_records_duration_and_reraises(
    monkeypatch,
    tmp_path,
):
    events = _gateway_duration_events(monkeypatch)
    gateway = CodeModeGateway(
        GatewayConfig(
            defaults=GatewayDefaults(workspace_root=tmp_path / "runs"),
        )
    )
    ctx = FakeGatewayContext()

    async def fake_run_code(code, env=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(gateway, "run_code", fake_run_code)

    try:
        with pytest.raises(RuntimeError, match="boom"):
            await gateway.execute_mcp_code("RESULT = 1", ctx)  # type: ignore[arg-type]
    finally:
        await gateway._sandbox.aclose()

    assert events == [("execute_mcp_code", "error")]


@pytest.mark.asyncio
async def test_gateway_script_error_records_duration_and_reraises(
    monkeypatch,
    tmp_path,
):
    events = _gateway_duration_events(monkeypatch)
    gateway = CodeModeGateway(
        GatewayConfig(
            defaults=GatewayDefaults(workspace_root=tmp_path / "runs"),
        )
    )
    ctx = FakeGatewayContext()

    async def fake_run_script(script_path, env=None):
        raise ValueError("bad script")

    monkeypatch.setattr(gateway, "run_script", fake_run_script)

    try:
        with pytest.raises(ValueError, match="bad script"):
            await gateway.execute_mcp_script(tmp_path / "script.py", ctx)  # type: ignore[arg-type]
    finally:
        await gateway._sandbox.aclose()

    assert events == [("execute_mcp_script", "error")]
