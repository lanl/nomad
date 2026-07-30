# OpenTelemetry

FastMCP owns MCP tracing and trace-context propagation. Nomad adds domain
metrics and child spans for work below that MCP boundary, such as code-mode
sandbox execution. Both use the same process-wide OpenTelemetry provider.

FastMCP instrumentation is active by default and is a no-op until an OTel SDK
is configured. Install Nomad's optional dependency extra to let Nomad configure
that SDK with OTLP/gRPC trace and metric exporters:

```bash
pip install "nomad-scifm[otel]"
```

The [demo project](https://github.com/lanl/nomad/tree/main/container/demo) and
its container image already include this extra.

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

Nomad configures a provider only when none exists. If the process was launched
with `opentelemetry-instrument` or an embedding application already installed a
provider, Nomad and FastMCP use that provider without replacing it. Nomad only
flushes providers it configured itself.

Use `FASTMCP_TELEMETRY_MODE` to control FastMCP tracing: `native` (the default)
creates MCP spans and propagates context, `propagation_only` propagates context
without MCP spans, and `off` disables both. Nomad's custom spans use FastMCP's
tracer and therefore follow the same mode.

For OTel environment variable behavior and exporter configuration, see the
[OpenTelemetry Python documentation](https://opentelemetry.io/docs/languages/python/)
and
[Python exporter documentation](https://opentelemetry.io/docs/languages/python/exporters/).

## Spans

FastMCP creates the server and client MCP spans, including `tools/call` spans
for SciFM tools, code-mode entrypoints, and upstream calls. Nomad does not wrap
those boundaries a second time. The code-mode gateway adds the
`nomad.gateway.sandbox.run` child span for sandbox execution details; Nomad
metrics continue to cover entrypoints, sandbox runs, and upstream calls.

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
