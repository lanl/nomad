from __future__ import annotations

import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import torch

try:
    from opentelemetry import metrics
    from opentelemetry.metrics import CallbackOptions, Observation
except ImportError:
    CallbackOptions = Any

    @dataclass(frozen=True)
    class Observation:
        value: int | float
        attributes: dict[str, Any] | None = None

    class _NoopInstrument:
        def add(self, *args: Any, **kwargs: Any) -> None:
            pass

        def record(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _NoopMeter:
        def create_counter(self, *args: Any, **kwargs: Any) -> _NoopInstrument:
            return _NoopInstrument()

        def create_histogram(self, *args: Any, **kwargs: Any) -> _NoopInstrument:
            return _NoopInstrument()

        def create_observable_gauge(self, *args: Any, **kwargs: Any) -> _NoopInstrument:
            return _NoopInstrument()

        def create_observable_up_down_counter(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> _NoopInstrument:
            return _NoopInstrument()

    class _NoopMetrics:
        @staticmethod
        def get_meter(name: str) -> _NoopMeter:
            return _NoopMeter()

    metrics = _NoopMetrics()

if TYPE_CHECKING:
    from .torch_tool_manager import TorchModelToolManager


MetricKind = Literal[
    "counter",
    "histogram",
    "observable gauge",
    "observable up/down counter",
]
MetricScope = Literal["serve", "gateway"]


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: str
    kind: MetricKind
    unit: str
    description: str
    scope: MetricScope


_METRIC_DEFINITIONS = (
    MetricDefinition(
        "nomad.tool.requests",
        "counter",
        "{request}",
        "Tool requests accepted by the Torch tool manager.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.request_errors",
        "counter",
        "{request}",
        "Tool requests completed with an execution error.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.request_cancellations",
        "counter",
        "{request}",
        "Tool requests cancelled while pending or executing.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.request_rejections",
        "counter",
        "{request}",
        "Tool requests rejected before enqueueing.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.request.duration",
        "histogram",
        "s",
        "End-to-end tool request duration, including queue wait.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.request.queue_wait",
        "histogram",
        "s",
        "Time a tool request waits before batch execution starts.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.batch.duration",
        "histogram",
        "s",
        "Wall time spent executing one model batch.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.batch.size",
        "histogram",
        "{request}",
        "Number of requests included in an executed model batch.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.batch.oom_reductions",
        "counter",
        "{event}",
        "Batch size reductions caused by out-of-memory errors.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.device.load_duration",
        "histogram",
        "s",
        "Time spent moving a tool onto a device.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.device.offloads",
        "counter",
        "{event}",
        "Tool offload operations to CPU.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.device.idle_evictions",
        "counter",
        "{event}",
        "Device allocations removed after a tool idle timeout.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.disk.loads",
        "counter",
        "{event}",
        "Tool load attempts from a registered source.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.disk.load_duration",
        "histogram",
        "s",
        "Wall time spent loading a tool from a registered source.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.disk.unloads",
        "counter",
        "{event}",
        "Resident CPU tool instances unloaded after a disk idle timeout.",
        "serve",
    ),
    MetricDefinition(
        "nomad.device.cache_clears",
        "counter",
        "{event}",
        "Accelerator cache clear attempts after server idle periods.",
        "serve",
    ),
    MetricDefinition(
        "nomad.device.cache_clear.duration",
        "histogram",
        "s",
        "Wall time spent clearing unused accelerator cache.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.queue.length",
        "observable up/down counter",
        "{request}",
        "Pending requests per managed tool.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.inflight.requests",
        "observable up/down counter",
        "{request}",
        "Requests currently assigned to executing batches.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.batch.configured_size",
        "observable up/down counter",
        "{request}",
        "Current effective batch size per managed tool.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.pending_tools",
        "observable up/down counter",
        "{tool}",
        "Number of tools waiting for scheduler dispatch.",
        "serve",
    ),
    MetricDefinition(
        "nomad.device.slot.busy",
        "observable gauge",
        "1",
        "Whether a managed device slot is busy.",
        "serve",
    ),
    MetricDefinition(
        "nomad.device.tool.loaded",
        "observable up/down counter",
        "1",
        "Whether a tool is resident on a managed device slot.",
        "serve",
    ),
    MetricDefinition(
        "nomad.tool.resident",
        "observable up/down counter",
        "1",
        "Whether a managed tool has a resident CPU instance.",
        "serve",
    ),
    MetricDefinition(
        "nomad.device.memory.allocated",
        "observable gauge",
        "By",
        "Best-effort accelerator memory currently allocated.",
        "serve",
    ),
    MetricDefinition(
        "nomad.device.memory.reserved",
        "observable gauge",
        "By",
        "Best-effort accelerator memory currently reserved by the backend.",
        "serve",
    ),
    MetricDefinition(
        "nomad.device.utilization",
        "observable gauge",
        "1",
        "Backend-reported device utilization ratio when available.",
        "serve",
    ),
    MetricDefinition(
        "nomad.gateway.requests",
        "counter",
        "{request}",
        "Code-mode gateway requests received.",
        "gateway",
    ),
    MetricDefinition(
        "nomad.gateway.request.duration",
        "histogram",
        "s",
        "Code-mode gateway request duration.",
        "gateway",
    ),
    MetricDefinition(
        "nomad.gateway.sandbox.duration",
        "histogram",
        "s",
        "Sandbox subprocess execution duration.",
        "gateway",
    ),
    MetricDefinition(
        "nomad.gateway.upstream_tool.calls",
        "counter",
        "{call}",
        "Upstream MCP tool calls made through the code-mode gateway.",
        "gateway",
    ),
    MetricDefinition(
        "nomad.gateway.upstream_tool.duration",
        "histogram",
        "s",
        "Duration of upstream MCP tool calls made through the gateway.",
        "gateway",
    ),
)
_METRIC_DEFINITIONS_BY_NAME = {
    definition.name: definition for definition in _METRIC_DEFINITIONS
}


def metric_definitions(
    scope: MetricScope | None = None,
) -> tuple[MetricDefinition, ...]:
    if scope is None:
        return _METRIC_DEFINITIONS
    return tuple(
        definition for definition in _METRIC_DEFINITIONS if definition.scope == scope
    )


def metrics_markdown_table(scope: MetricScope | None = None) -> str:
    rows = [
        "| Metric | Type | Unit | Description |",
        "| --- | --- | --- | --- |",
    ]
    for definition in metric_definitions(scope):
        rows.append(
            "| "
            f"`{definition.name}` | "
            f"{definition.kind} | "
            f"`{definition.unit}` | "
            f"{definition.description} |"
        )
    return "\n".join(rows)


meter = metrics.get_meter("nomad")


def _create_instrument(name: str):
    definition = _METRIC_DEFINITIONS_BY_NAME[name]
    if definition.kind == "counter":
        return meter.create_counter(
            definition.name,
            unit=definition.unit,
            description=definition.description,
        )
    if definition.kind == "histogram":
        return meter.create_histogram(
            definition.name,
            unit=definition.unit,
            description=definition.description,
        )
    raise ValueError(f"Metric '{name}' is not a synchronous instrument")


tool_requests = _create_instrument("nomad.tool.requests")
tool_request_errors = _create_instrument("nomad.tool.request_errors")
tool_request_cancellations = _create_instrument("nomad.tool.request_cancellations")
tool_request_rejections = _create_instrument("nomad.tool.request_rejections")
tool_request_duration = _create_instrument("nomad.tool.request.duration")
tool_queue_wait = _create_instrument("nomad.tool.request.queue_wait")
tool_batch_duration = _create_instrument("nomad.tool.batch.duration")
tool_batch_size = _create_instrument("nomad.tool.batch.size")
tool_batch_reductions = _create_instrument("nomad.tool.batch.oom_reductions")
tool_device_load_duration = _create_instrument("nomad.tool.device.load_duration")
tool_device_offloads = _create_instrument("nomad.tool.device.offloads")
tool_device_idle_evictions = _create_instrument("nomad.tool.device.idle_evictions")
tool_disk_loads = _create_instrument("nomad.tool.disk.loads")
tool_disk_load_duration = _create_instrument("nomad.tool.disk.load_duration")
tool_disk_unloads = _create_instrument("nomad.tool.disk.unloads")
device_cache_clears = _create_instrument("nomad.device.cache_clears")
device_cache_clear_duration = _create_instrument("nomad.device.cache_clear.duration")
gateway_requests = _create_instrument("nomad.gateway.requests")
gateway_request_duration = _create_instrument("nomad.gateway.request.duration")
gateway_sandbox_duration = _create_instrument("nomad.gateway.sandbox.duration")
gateway_upstream_tool_calls = _create_instrument("nomad.gateway.upstream_tool.calls")
gateway_upstream_tool_duration = _create_instrument(
    "nomad.gateway.upstream_tool.duration"
)

_managers: weakref.WeakSet[TorchModelToolManager] = weakref.WeakSet()


def _device_attributes(device: torch.device) -> dict[str, Any]:
    return {
        "device": str(device),
        "device.type": device.type,
        "device.index": device.index if device.index is not None else -1,
    }


def _tool_attributes(tool_name: str) -> dict[str, Any]:
    return {"tool": tool_name}


def tool_device_attributes(tool_name: str, device: torch.device) -> dict[str, Any]:
    return {**_tool_attributes(tool_name), **_device_attributes(device)}


def register_tool_manager(manager: TorchModelToolManager) -> None:
    _managers.add(manager)


def record_tool_request(tool_name: str) -> None:
    tool_requests.add(1, _tool_attributes(tool_name))


def record_tool_request_rejection(tool_name: str, reason: str) -> None:
    tool_request_rejections.add(1, {**_tool_attributes(tool_name), "reason": reason})


def record_tool_request_cancellation(tool_name: str) -> None:
    tool_request_cancellations.add(1, _tool_attributes(tool_name))


def record_tool_queue_wait(tool_name: str, seconds: float) -> None:
    tool_queue_wait.record(seconds, _tool_attributes(tool_name))


def record_tool_request_duration(
    tool_name: str,
    seconds: float,
    *,
    status: str,
) -> None:
    attributes = {**_tool_attributes(tool_name), "status": status}
    tool_request_duration.record(seconds, attributes)
    if status == "error":
        tool_request_errors.add(1, _tool_attributes(tool_name))


def record_tool_batch(
    tool_name: str,
    device: torch.device,
    *,
    batch_size: int,
    duration_seconds: float,
    status: str,
) -> None:
    attributes = {**tool_device_attributes(tool_name, device), "status": status}
    tool_batch_size.record(batch_size, attributes)
    tool_batch_duration.record(duration_seconds, attributes)


def record_tool_batch_reduction(
    tool_name: str,
    device: torch.device,
    *,
    old_batch_size: int,
    new_batch_size: int,
) -> None:
    tool_batch_reductions.add(
        1,
        {
            **tool_device_attributes(tool_name, device),
            "old_batch_size": old_batch_size,
            "new_batch_size": new_batch_size,
        },
    )


def record_tool_device_load(
    tool_name: str,
    device: torch.device,
    duration_seconds: float,
) -> None:
    tool_device_load_duration.record(
        duration_seconds,
        tool_device_attributes(tool_name, device),
    )


def record_tool_offload(tool_name: str) -> None:
    tool_device_offloads.add(1, _tool_attributes(tool_name))


def record_tool_idle_eviction(tool_name: str, device: torch.device) -> None:
    tool_device_idle_evictions.add(1, tool_device_attributes(tool_name, device))


def record_tool_disk_load(
    tool_name: str,
    duration_seconds: float,
    *,
    load_kind: str,
    status: str,
) -> None:
    attributes = {
        **_tool_attributes(tool_name),
        "load.kind": load_kind,
        "status": status,
    }
    tool_disk_loads.add(1, attributes)
    tool_disk_load_duration.record(duration_seconds, attributes)


def record_tool_disk_unload(tool_name: str) -> None:
    tool_disk_unloads.add(1, _tool_attributes(tool_name))


def record_device_cache_clear(duration_seconds: float, *, status: str) -> None:
    attributes = {"status": status}
    device_cache_clears.add(1, attributes)
    device_cache_clear_duration.record(duration_seconds, attributes)


def record_gateway_request(kind: str) -> None:
    gateway_requests.add(1, {"request.kind": kind})


def record_gateway_request_duration(
    kind: str,
    seconds: float,
    *,
    status: str,
) -> None:
    gateway_request_duration.record(
        seconds,
        {
            "request.kind": kind,
            "status": status,
        },
    )


def record_gateway_sandbox_duration(
    seconds: float,
    *,
    status: str,
    returncode: int | None,
) -> None:
    gateway_sandbox_duration.record(
        seconds,
        {
            "status": status,
            "returncode": returncode if returncode is not None else -1,
        },
    )


def record_gateway_upstream_tool_call(
    server: str,
    tool: str,
    seconds: float,
    *,
    status: str,
) -> None:
    attributes = {
        "server": server,
        "tool": tool,
        "status": status,
    }
    gateway_upstream_tool_calls.add(1, attributes)
    gateway_upstream_tool_duration.record(seconds, attributes)


def _queue_length_observations(_options: CallbackOptions):
    for manager in list(_managers):
        yield from manager.queue_length_observations()


def _inflight_observations(_options: CallbackOptions):
    for manager in list(_managers):
        yield from manager.inflight_observations()


def _configured_batch_size_observations(_options: CallbackOptions):
    for manager in list(_managers):
        yield from manager.configured_batch_size_observations()


def _pending_tools_observations(_options: CallbackOptions):
    for manager in list(_managers):
        yield from manager.pending_tools_observations()


def _device_busy_observations(_options: CallbackOptions):
    for manager in list(_managers):
        yield from manager.device_busy_observations()


def _loaded_tool_observations(_options: CallbackOptions):
    for manager in list(_managers):
        yield from manager.loaded_tool_observations()


def _resident_tool_observations(_options: CallbackOptions):
    for manager in list(_managers):
        yield from manager.resident_tool_observations()


def _device_memory_allocated_observations(_options: CallbackOptions):
    for manager in list(_managers):
        yield from manager.device_memory_allocated_observations()


def _device_memory_reserved_observations(_options: CallbackOptions):
    for manager in list(_managers):
        yield from manager.device_memory_reserved_observations()


def _device_utilization_observations(_options: CallbackOptions):
    for manager in list(_managers):
        yield from manager.device_utilization_observations()


for _metric_name, _callbacks in (
    ("nomad.tool.queue.length", [_queue_length_observations]),
    ("nomad.tool.inflight.requests", [_inflight_observations]),
    ("nomad.tool.batch.configured_size", [_configured_batch_size_observations]),
    ("nomad.tool.pending_tools", [_pending_tools_observations]),
    ("nomad.device.slot.busy", [_device_busy_observations]),
    ("nomad.device.tool.loaded", [_loaded_tool_observations]),
    ("nomad.tool.resident", [_resident_tool_observations]),
    ("nomad.device.memory.allocated", [_device_memory_allocated_observations]),
    ("nomad.device.memory.reserved", [_device_memory_reserved_observations]),
    ("nomad.device.utilization", [_device_utilization_observations]),
):
    _definition = _METRIC_DEFINITIONS_BY_NAME[_metric_name]
    if _definition.kind == "observable up/down counter":
        meter.create_observable_up_down_counter(
            _definition.name,
            callbacks=_callbacks,
            unit=_definition.unit,
            description=_definition.description,
        )
    else:
        meter.create_observable_gauge(
            _definition.name,
            callbacks=_callbacks,
            unit=_definition.unit,
            description=_definition.description,
        )
