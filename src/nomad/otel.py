from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_CONFIGURED_PROVIDER: Any | None = None
_CONFIGURED_METER_PROVIDER: Any | None = None
_MISSING_OTEL_WARNING_EMITTED = False


class NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass

    def end(self) -> None:
        pass


class NoopTracer:
    def start_span(self, *args: Any, **kwargs: Any) -> NoopSpan:
        return NoopSpan()

    @contextlib.contextmanager
    def start_as_current_span(self, *args: Any, **kwargs: Any):
        yield NoopSpan()


def _import_trace_api():
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace


def get_tracer(name: str):
    trace = _import_trace_api()
    if trace is None:
        return NoopTracer()
    return trace.get_tracer(name)


def _truthy(value: str | None) -> bool:
    return value is not None and value.lower() in {"1", "true", "yes", "on"}


def _otel_requested(enabled: bool | None) -> bool:
    if enabled is not None:
        return enabled
    nomad_enabled = os.environ.get("NOMAD_OTEL_ENABLED")
    if nomad_enabled is not None:
        return _truthy(nomad_enabled)
    traces_exporter = os.environ.get("OTEL_TRACES_EXPORTER")
    if traces_exporter is not None:
        return traces_exporter.lower() not in {"", "none", "false", "off"}
    metrics_exporter = os.environ.get("OTEL_METRICS_EXPORTER")
    if metrics_exporter is not None:
        return metrics_exporter.lower() not in {"", "none", "false", "off"}
    return any(
        os.environ.get(name)
        for name in (
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        )
    )


def configure_otel(
    *,
    service_name: str,
    service_version: str,
    enabled: bool | None = None,
    otlp_endpoint: str | None = None,
) -> bool:
    """Configure OTLP trace and metric exporters when telemetry is requested."""

    global \
        _CONFIGURED_METER_PROVIDER, \
        _CONFIGURED_PROVIDER, \
        _MISSING_OTEL_WARNING_EMITTED
    if _CONFIGURED_PROVIDER is not None and _CONFIGURED_METER_PROVIDER is not None:
        return True
    if _truthy(os.environ.get("OTEL_SDK_DISABLED")):
        return False
    if not _otel_requested(enabled):
        return False

    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        if not _MISSING_OTEL_WARNING_EMITTED:
            logger.warning(
                "OpenTelemetry requested but optional dependencies are not installed. "
                "Install nomad[otel] to enable telemetry export."
            )
            _MISSING_OTEL_WARNING_EMITTED = True
        return False

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
        }
    )
    exporter_kwargs = {}
    if otlp_endpoint:
        exporter_kwargs["endpoint"] = otlp_endpoint

    configured = False
    existing_provider = trace.get_tracer_provider()
    if _CONFIGURED_PROVIDER is None and existing_provider.__class__.__name__ == (
        "ProxyTracerProvider"
    ):
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(**exporter_kwargs))
        )
        trace.set_tracer_provider(provider)
        _CONFIGURED_PROVIDER = provider
        configured = True
    elif isinstance(existing_provider, TracerProvider):
        logger.debug("OpenTelemetry tracer provider already configured.")
    else:
        logger.debug("OpenTelemetry tracer provider already configured.")

    existing_meter_provider = metrics.get_meter_provider()
    if (
        _CONFIGURED_METER_PROVIDER is None
        and existing_meter_provider.__class__.__name__ == ("_ProxyMeterProvider")
    ):
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(**exporter_kwargs)
        )
        meter_provider = MeterProvider(
            metric_readers=[metric_reader],
            resource=resource,
        )
        metrics.set_meter_provider(meter_provider)
        _CONFIGURED_METER_PROVIDER = meter_provider
        configured = True
    elif isinstance(existing_meter_provider, MeterProvider):
        logger.debug("OpenTelemetry meter provider already configured.")
    else:
        logger.debug("OpenTelemetry meter provider already configured.")

    if configured:
        logger.info("OpenTelemetry configured for service '%s'.", service_name)
    return configured


def set_span_ok(span: Any) -> None:
    try:
        from opentelemetry.trace import Status, StatusCode
    except ImportError:
        span.set_status("OK")
        return
    span.set_status(Status(StatusCode.OK))


def set_span_error(span: Any, exc: Exception | str) -> None:
    if isinstance(exc, Exception):
        span.record_exception(exc)
    try:
        from opentelemetry.trace import Status, StatusCode
    except ImportError:
        span.set_status(str(exc))
        return
    message = str(exc)
    span.set_status(Status(StatusCode.ERROR, message))


def _force_flush(provider: Any, provider_name: str) -> None:
    force_flush = getattr(provider, "force_flush", None)
    if force_flush is None:
        return
    try:
        force_flush()
    except Exception:  # pragma: no cover - exporter failures are backend-specific
        logger.debug("Failed to flush OpenTelemetry %s.", provider_name, exc_info=True)


def shutdown_otel() -> None:
    """Flush OTel providers configured by Nomad, if any."""

    provider = _CONFIGURED_PROVIDER
    if provider is not None:
        _force_flush(provider, "tracer provider")
    meter_provider = _CONFIGURED_METER_PROVIDER
    if meter_provider is not None:
        _force_flush(meter_provider, "meter provider")
