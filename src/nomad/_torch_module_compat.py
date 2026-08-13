from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from docket import ConcurrencyLimit
from docket.dependencies import current_execution
from fastmcp import FastMCP
from fastmcp.tools import FunctionTool
from fastmcp.utilities.tasks import TaskConfig


class _WorkerScopedConcurrencyLimit(ConcurrencyLimit):
    """Docket's ConcurrencyLimit requires a worker execution context.

    FastMCP resolves Dependency-typed defaults on plain tools/call too,
    where no docket worker exists; there the limit must read as inert.
    The manager's own queue backpressure governs the direct path.
    """

    async def __aenter__(self) -> ConcurrencyLimit:
        if current_execution.get(None) is None:
            return self
        return await super().__aenter__()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        if current_execution.get(None) is None:
            return None
        return await super().__aexit__(exc_type, exc_value, traceback)


def _compatible_torch_module_tool_types() -> tuple[type[Any], ...]:
    from .fm_base_tool import TorchModuleTool

    tool_types: list[type[Any]] = [TorchModuleTool]
    try:
        from ursa.tools.fm_base_tool import TorchModuleTool as UrsaTorchModuleTool
    except Exception:
        return tuple(tool_types)

    if UrsaTorchModuleTool not in tool_types:
        tool_types.append(UrsaTorchModuleTool)
    return tuple(tool_types)


def is_torch_module_tool_instance(value: object) -> bool:
    return isinstance(value, _compatible_torch_module_tool_types())


def build_torch_module_fastmcp_tool(
    tool: Any,
    *,
    invoke: Callable[[Any], Any] | None = None,
    task_concurrency_limit: int | None = None,
    task_admission: Any = None,
) -> FunctionTool:
    """Build a FastMCP tool from a TorchModuleTool-like object."""

    concurrency = (
        _WorkerScopedConcurrencyLimit(max_concurrent=task_concurrency_limit)
        if task_concurrency_limit is not None
        else None
    )

    async def fn(
        _task_concurrency: ConcurrencyLimit | None = concurrency,
        _task_admission: Any = task_admission,
        **input_data: Any,
    ) -> Any:
        args = tool.args_schema(**input_data)
        result = invoke(args) if invoke is not None else tool(args)
        if inspect.isawaitable(result):
            return await result
        return result

    return FunctionTool(
        fn=fn,
        name=tool.name,
        description=tool.description,
        parameters=tool.args_schema.model_json_schema(),
        output_schema=tool.output_schema.model_json_schema(),
        task_config=TaskConfig(mode="optional"),
    )


def add_torch_module_tool_to_fastmcp(
    server: FastMCP,
    tool: Any,
    *,
    invoke: Callable[[Any], Any] | None = None,
) -> FunctionTool:
    """Register a TorchModuleTool-like object using FastMCP's public API."""
    return server.add_tool(build_torch_module_fastmcp_tool(tool, invoke=invoke))
