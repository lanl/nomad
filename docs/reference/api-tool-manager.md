# Tool Manager Reference

The tool manager is the runtime that makes multiple
{py:class}`nomad.fm_base_tool.TorchModuleTool` models practical to serve from a
single Nomad process. It keeps track of available devices, loads tools onto an
accelerator when they need to run, and can offload idle tools back to CPU so
GPU memory can be shared across a larger set of models.

It also queues requests, batches calls for tools that support batching, and can
register the managed tools with FastMCP.

Nomad implements the MCP Tasks extension in the server process. A client that
negotiates task support receives a task handle immediately and can poll or
cancel the task with the standard task methods. Clients that do not negotiate
the extension continue to call the same tool synchronously. Both paths invoke
the same managed-tool callable and enter the same batching and accelerator
queue, whose per-tool capacity is controlled by
{py:attr}`~nomad.config.ToolManagerConfig.max_pending_per_tool`.

Task state is deliberately process-local: it survives a client disconnect but
not a Nomad server restart. Records expire
{py:attr}`~nomad.config.ToolManagerConfig.task_ttl_seconds` after submission,
including tasks that are still running, and the registry is capped by
{py:attr}`~nomad.config.ToolManagerConfig.task_max_records`. The defaults retain
at most 65,536 task records for 15 minutes. This bounds both background work and
the result payloads held in memory without requiring a separate task broker.
At shutdown, Nomad requests cancellation and waits up to
{py:attr}`~nomad.config.ToolManagerConfig.task_shutdown_timeout_seconds` before
continuing server teardown.

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
