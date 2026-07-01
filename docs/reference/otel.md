# OpenTelemetry

Nomad emits OpenTelemetry metrics and spans for model serving and the code-mode
gateway when telemetry export is enabled. Install the optional dependency extra
to include the OTel SDK and OTLP/gRPC exporter:

```bash
pip install "nomad[otel]"
```

## Enabling export

Nomad configures OTLP/gRPC trace and metric exporters when any of these are set:

- `telemetry.enabled: true` in {py:class}`nomad.config.ServerConfig` or
  {py:class}`nomad.gateway.config.GatewayConfig`.
- `NOMAD_OTEL_ENABLED=true`.
- Standard OTel environment variables such as `OTEL_EXPORTER_OTLP_ENDPOINT`,
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`,
  `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`, `OTEL_TRACES_EXPORTER`, or
  `OTEL_METRICS_EXPORTER`.

Set `telemetry.service_name` or `OTEL_SERVICE_NAME` to control the exported
service name. Set `telemetry.otlp_endpoint` or `OTEL_EXPORTER_OTLP_ENDPOINT` to
control the collector endpoint. Set `OTEL_SDK_DISABLED=true` to disable SDK
setup.

For OTel environment variable behavior and exporter configuration, see the
[OpenTelemetry Python documentation](https://opentelemetry.io/docs/languages/python/)
and
[Python exporter documentation](https://opentelemetry.io/docs/languages/python/exporters/).

## Spans

`nomad serve` creates spans around managed Torch tool requests and batch
execution. The code-mode gateway creates spans for MCP tool entrypoints, sandbox
runs, and upstream MCP tool calls.

## Model-serving metrics

The table is generated from
{repo_file}`src/nomad/metrics.py <src/nomad/metrics.py>`.

```{include} generated/metrics.md
:start-after: <!-- nomad-server-metrics-start -->
:end-before: <!-- nomad-server-metrics-end -->
```

## Gateway metrics

The gateway metrics cover MCP entrypoints, sandbox execution, and upstream MCP
tool calls.

```{include} generated/metrics.md
:start-after: <!-- nomad-gateway-metrics-start -->
:end-before: <!-- nomad-gateway-metrics-end -->
```
