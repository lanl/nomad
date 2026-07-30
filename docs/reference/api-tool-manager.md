# Tool Manager Reference

The tool manager is the runtime that makes multiple
{py:class}`nomad.fm_base_tool.TorchModuleTool` models practical to serve from a
single Nomad process. It keeps track of available devices, loads tools onto an
accelerator when they need to run, and can offload idle tools back to CPU so
GPU memory can be shared across a larger set of models.

It also queues requests, batches calls for tools that support batching, and can
register the managed tools with FastMCP.

FastMCP background-task workers feed requests into this queue; they do not
schedule accelerator work themselves. Nomad derives concurrency by adding the
total per-tool pending capacity to the
device pipeline capacity: `number_of_tools * max_pending_per_tool + devices *
max_batch_size * device_queue_depth`. It then applies
{py:attr}`~nomad.config.ToolManagerConfig.task_min_concurrency` as a floor.
`max_pending_per_tool` defaults to 65,536. Docket limits each SciFM task to 75%
of that capacity before invoking Nomad, leaving headroom for foreground calls;
each admitted background task also atomically reserves a real Nomad queue slot.
If no slot is available, Docket reschedules the task without removing it from
the queue. The Nomad limit remains a defensive boundary that returns a `Server
busy` tool error. The device queue depth and minimum default to two and ten,
respectively. This keeps enough work available for batching without moving GPU
scheduling out of the tool manager.

Docket retains a completed or failed task's result payload for 15 minutes by
default. This lifetime starts when the task completes or fails, not when it is
submitted; for example, a task that runs for an hour still has its result
available for approximately 15 minutes after it finishes.

For managed Torch tools, the manager keeps one resident CPU instance when the
tool is loaded from configuration and may create additional tool instances from
the configured source when a busy tool is assigned to more than one device slot.
Idle tools scale down one device allocation per
{py:attr}`~nomad.config.ToolManagerConfig.idle_seconds` interval. After a tool
is fully removed from device slots,
{py:attr}`~nomad.config.ToolManagerConfig.disk_idle_seconds` controls when the
resident CPU instance may be dropped; a later request reloads it with
`TorchModuleTool.from_pretrained(source)`. Separately,
{py:attr}`~nomad.config.ToolManagerConfig.gc_idle_seconds` controls when unused
Python and accelerator caches are cleared while the server is idle.

```{eval-rst}
.. automodule:: nomad.torch_tool_manager
```
