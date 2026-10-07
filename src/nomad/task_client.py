"""Python SDK client support for Nomad's SEP-2663 task extension."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence
from typing import Any

import mcp_types
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
from mcp.client.extension import ClaimContext, ClientExtension, ResultClaim
from mcp.shared.exceptions import MCPError

from .task_protocol import (
    TASKS_PROTOCOL_VERSIONS,
    CancelTaskParams,
    CancelTaskRequest,
    CancelTaskResult,
    CreateTaskResult,
    GetTaskParams,
    GetTaskRequest,
    GetTaskResult,
)

_MIN_POLL_SECONDS = 0.02
_BEST_EFFORT_CANCEL_TIMEOUT_SECONDS = 1.0


class TaskCancelledError(RuntimeError):
    """The remote task reached the ``cancelled`` terminal state."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task {task_id} was cancelled")


class TaskTimeoutError(TimeoutError):
    """Polling timed out while the remote task remains independently addressable."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task {task_id} did not complete in time")


class NomadTasksClientExtension(ClientExtension):
    """Transparent Python SDK polling for task-augmented tool calls."""

    identifier = TASKS_EXTENSION_ID

    def claims(self) -> Sequence[ResultClaim[Any]]:
        return (
            ResultClaim(
                result_type="task",
                model=CreateTaskResult,
                resolve=_resolve_task,
                protocol_versions=TASKS_PROTOCOL_VERSIONS,
            ),
        )


async def resolve_task(
    created: CreateTaskResult,
    session: Any,
    *,
    read_timeout_seconds: float | None,
    cancel_on_caller_cancellation: bool = True,
) -> mcp_types.CallToolResult:
    """Poll one task to completion while preserving one total timeout budget."""
    deadline = (
        time.monotonic() + read_timeout_seconds
        if read_timeout_seconds is not None
        else None
    )
    poll_interval_ms = created.poll_interval_ms
    try:
        while True:
            if poll_interval_ms is not None:
                delay = max(_MIN_POLL_SECONDS, poll_interval_ms / 1000)
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TaskTimeoutError(created.task_id)
                    await asyncio.sleep(min(delay, remaining))
                else:
                    await asyncio.sleep(delay)

            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TaskTimeoutError(created.task_id)
            request = session.send_request(
                GetTaskRequest(params=GetTaskParams(task_id=created.task_id)),
                GetTaskResult,
                request_read_timeout_seconds=remaining,
            )
            try:
                result = (
                    await request
                    if remaining is None
                    else await asyncio.wait_for(request, timeout=remaining)
                )
            except MCPError as exc:
                if deadline is not None and exc.code == mcp_types.REQUEST_TIMEOUT:
                    raise TaskTimeoutError(created.task_id) from exc
                raise
            except TimeoutError as exc:
                raise TaskTimeoutError(created.task_id) from exc
            if result.status == "completed":
                # GetTaskResult validates this invariant; the guard keeps a useful
                # error if an alternate SDK model bypasses that validation.
                if result.result is None:
                    raise MCPError(
                        code=mcp_types.INTERNAL_ERROR,
                        message=f"Task {created.task_id} completed without a result",
                    )
                return mcp_types.CallToolResult.model_validate(result.result)
            if result.status == "failed":
                error = result.error or mcp_types.ErrorData(
                    code=mcp_types.INTERNAL_ERROR,
                    message=result.status_message or "Task failed",
                )
                raise MCPError(
                    code=error.code,
                    message=error.message,
                    data=error.data,
                )
            if result.status == "cancelled":
                raise TaskCancelledError(created.task_id)
            if result.status == "input_required":
                raise MCPError(
                    code=mcp_types.INTERNAL_ERROR,
                    message="Nomad model tasks do not support input-required execution",
                )
            poll_interval_ms = (
                result.poll_interval_ms
                if result.poll_interval_ms is not None
                else created.poll_interval_ms
            )
            if poll_interval_ms is None:
                poll_interval_ms = 1000
    except asyncio.CancelledError:
        if cancel_on_caller_cancellation:
            await _cancel_task_best_effort(session, created.task_id)
        raise


async def _resolve_task(
    created: CreateTaskResult,
    context: ClaimContext,
) -> mcp_types.CallToolResult:
    return await resolve_task(
        created,
        context.session,
        read_timeout_seconds=context.read_timeout_seconds,
    )


async def _cancel_task_best_effort(session: Any, task_id: str) -> None:
    request = asyncio.create_task(
        session.send_request(
            CancelTaskRequest(params=CancelTaskParams(task_id=task_id)),
            CancelTaskResult,
            request_read_timeout_seconds=_BEST_EFFORT_CANCEL_TIMEOUT_SECONDS,
        )
    )
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await asyncio.wait_for(
            asyncio.shield(request),
            timeout=_BEST_EFFORT_CANCEL_TIMEOUT_SECONDS,
        )
