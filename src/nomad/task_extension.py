"""SQLite-backed server implementation of the SEP-2663 Tasks extension."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mcp_types
from fastmcp.exceptions import FastMCPError, NotFoundError
from fastmcp.server.dependencies import extract_version_spec, get_http_request
from fastmcp.server.extensions import (
    MethodBinding,
    ServerExtension,
    read_client_extension_settings,
)
from fastmcp.tools.base import InputRequiredToolResult, ToolResult
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
from fastmcp.utilities.versions import VersionSpec
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import MCP_NAME_HEADER, decode_header_value
from mcp_types.jsonrpc import (
    HEADER_MISMATCH,
    MISSING_REQUIRED_CLIENT_CAPABILITY,
)

from . import metrics as nomad_metrics
from .task_client import NomadTasksClientExtension
from .task_protocol import (
    TASKS_PROTOCOL_VERSION,
    TASKS_PROTOCOL_VERSIONS,
    CancelTaskParams,
    CancelTaskRequest,
    CancelTaskResult,
    CreateTaskResult,
    GetTaskParams,
    GetTaskRequest,
    GetTaskResult,
    TaskStatus,
    UpdateTaskParams,
    UpdateTaskRequest,
    UpdateTaskResult,
)
from .task_store import (
    SQLiteTaskStore,
    StoredTask,
    TaskCapacityError,
    TaskStore,
    now_iso,
    task_owner_key,
)

if TYPE_CHECKING:
    from fastmcp.server.context import Context
    from fastmcp.server.extensions import ToolCallContinuation, ToolCallOutcome

logger = logging.getLogger(__name__)

_SERVER_BUSY = -32000
_TASK_STORAGE_ERROR = -32000
_DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
_DEFAULT_POLL_INTERVAL_MS = 1000


def _missing_capability_data() -> dict[str, Any]:
    return {
        "requiredCapabilities": {
            "extensions": {TASKS_EXTENSION_ID: {}},
        }
    }


def _task_not_found(task_id: str) -> MCPError:
    return MCPError(
        code=mcp_types.INVALID_PARAMS,
        message=f"Task {task_id} not found",
    )


def _tool_result_payload(outcome: ToolResult) -> dict[str, Any]:
    value = outcome.to_mcp_result()
    if isinstance(value, mcp_types.CallToolResult):
        result = value
    elif isinstance(value, tuple):
        content, structured_content = value
        result = mcp_types.CallToolResult(
            content=content,
            structured_content=structured_content,
        )
    else:
        result = mcp_types.CallToolResult(content=value)
    return result.model_dump(by_alias=True, mode="json", exclude_none=True)


def _tool_error_payload(error: FastMCPError) -> dict[str, Any]:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=str(error))],
        is_error=True,
    ).model_dump(by_alias=True, mode="json", exclude_none=True)


def _serialize_input(
    params: mcp_types.CallToolRequestParams,
    *,
    version: str | None,
) -> bytes:
    payload: dict[str, Any] = {
        "method": "tools/call",
        "params": {
            "name": params.name,
            "arguments": params.arguments or {},
        },
    }
    if version is not None:
        payload["params"]["version"] = version
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class NomadTasksExtension(ServerExtension):
    """Persist and queue every named model call, exposing handles when negotiated."""

    identifier = TASKS_EXTENSION_ID

    def __init__(
        self,
        *,
        tool_names: Collection[str],
        ttl_seconds: float = 15 * 60,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        shutdown_timeout_seconds: float = 5.0,
        store_path: str | Path = ":memory:",
        store: TaskStore | None = None,
    ) -> None:
        if shutdown_timeout_seconds < 0:
            raise ValueError("shutdown_timeout_seconds cannot be negative")
        if store is not None and store_path != ":memory:":
            raise ValueError("store and store_path cannot both be configured")
        self._tool_names = frozenset(tool_names)
        self._store = store or SQLiteTaskStore(
            store_path,
            ttl_seconds=ttl_seconds,
            max_bytes=max_bytes,
        )
        self._ttl_seconds = ttl_seconds
        self._ttl_ms = self._store.ttl_ms
        self._max_bytes = max_bytes
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._runners: dict[str, asyncio.Task[None]] = {}
        self._started_at: dict[str, float] = {}
        self._janitor: asyncio.Task[None] | None = None
        self._shutting_down = False

    def settings(self) -> dict[str, Any]:
        return {}

    def methods(self) -> Sequence[MethodBinding]:
        return (
            MethodBinding(
                method="tasks/get",
                params_type=GetTaskParams,
                handler=self._handle_get,
                protocol_versions=TASKS_PROTOCOL_VERSIONS,
            ),
            MethodBinding(
                method="tasks/update",
                params_type=UpdateTaskParams,
                handler=self._handle_update,
                protocol_versions=TASKS_PROTOCOL_VERSIONS,
            ),
            MethodBinding(
                method="tasks/cancel",
                params_type=CancelTaskParams,
                handler=self._handle_cancel,
                protocol_versions=TASKS_PROTOCOL_VERSIONS,
            ),
        )

    async def intercept_tool_call(
        self,
        params: mcp_types.CallToolRequestParams,
        context: Context,
        call_next: ToolCallContinuation,
    ) -> ToolCallOutcome:
        if params.name not in self._tool_names:
            return await call_next()

        version_str = extract_version_spec(params.meta)
        version = VersionSpec(eq=version_str) if version_str else None
        try:
            tool = await context.fastmcp.get_tool(params.name, version)
        except NotFoundError:
            tool = None
        if tool is None:
            return await call_next()

        supports_tasks = tool.task_config.supports_tasks()
        request_context = context.request_context
        opted_in = (
            supports_tasks
            and request_context is not None
            and request_context.protocol_version == TASKS_PROTOCOL_VERSION
            and context.client_extension_settings(TASKS_EXTENSION_ID) is not None
        )
        mode = tool.task_config.mode
        if mode == "required" and not opted_in:
            raise MCPError(
                code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                message=(
                    f"Tool {tool.name!r} requires the tasks extension; "
                    "the client did not declare it."
                ),
                data=_missing_capability_data(),
            )

        poll_interval_ms = (
            max(
                20,
                int(tool.task_config.poll_interval.total_seconds() * 1000),
            )
            if supports_tasks
            else _DEFAULT_POLL_INTERVAL_MS
        )
        await self._prune_expired()
        record = await self._create_record(
            params,
            version=version_str,
            poll_interval_ms=poll_interval_ms,
        )
        if opted_in and mode in {"optional", "required"}:
            self._start_exposed(record, call_next)
            return self._create_result(record)
        return await self._execute_inline(record, call_next)

    async def _create_record(
        self,
        params: mcp_types.CallToolRequestParams,
        *,
        version: str | None,
        poll_interval_ms: int,
    ) -> StoredTask:
        task_id = secrets.token_urlsafe(32)
        try:
            record, evicted = await self._store.create(
                task_id=task_id,
                owner_key=task_owner_key(),
                method="tools/call",
                input_json=_serialize_input(params, version=version),
                poll_interval_ms=poll_interval_ms,
            )
        except TaskCapacityError as exc:
            raise MCPError(
                code=_SERVER_BUSY,
                message="Server busy: task state byte budget exhausted",
            ) from exc
        self._started_at[task_id] = time.monotonic()
        self._cancel_runners(evicted)
        return record

    def _start_exposed(
        self,
        record: StoredTask,
        call_next: ToolCallContinuation,
    ) -> None:
        runner = asyncio.create_task(
            self._execute_exposed(record, call_next),
            name=f"nomad-mcp-task-{record.task_id}",
        )
        self._runners[record.task_id] = runner

        def runner_done(done: asyncio.Task[None]) -> None:
            if self._runners.get(record.task_id) is done:
                self._runners.pop(record.task_id, None)

        runner.add_done_callback(runner_done)

    async def _execute_inline(
        self,
        record: StoredTask,
        call_next: ToolCallContinuation,
    ) -> ToolCallOutcome:
        try:
            outcome = await call_next()
            if isinstance(outcome, InputRequiredToolResult):
                raise RuntimeError(
                    "Managed model tools do not support input-required results"
                )
            if not isinstance(outcome, ToolResult):
                raise RuntimeError(
                    f"Task returned unsupported result {type(outcome).__name__}"
                )
            status = await self._complete(record, _tool_result_payload(outcome))
            if status != "completed":
                raise MCPError(
                    code=_TASK_STORAGE_ERROR,
                    message="Task result exceeds the task state byte budget",
                )
            return outcome
        except asyncio.CancelledError:
            if not self._shutting_down:
                await self._cancel(record)
            raise
        except MCPError as exc:
            if not self._shutting_down:
                await self._fail(
                    record,
                    code=exc.code,
                    message=exc.message,
                    data=exc.data,
                )
            raise
        except FastMCPError as exc:
            if not self._shutting_down:
                await self._complete(record, _tool_error_payload(exc))
            raise
        except Exception as exc:
            if not self._shutting_down:
                await self._fail(
                    record,
                    code=mcp_types.INTERNAL_ERROR,
                    message=str(exc) or type(exc).__name__,
                )
            raise
        finally:
            if not self._shutting_down:
                await self._store.delete(record.task_id, reason="delivered")
            self._started_at.pop(record.task_id, None)

    async def _execute_exposed(
        self,
        record: StoredTask,
        call_next: ToolCallContinuation,
    ) -> None:
        try:
            outcome = await call_next()
            if self._shutting_down:
                return
            if isinstance(outcome, InputRequiredToolResult):
                raise RuntimeError(
                    "Task-backed model tools do not support input-required results"
                )
            if not isinstance(outcome, ToolResult):
                raise RuntimeError(
                    f"Task returned unsupported result {type(outcome).__name__}"
                )
            await self._complete(record, _tool_result_payload(outcome))
        except asyncio.CancelledError:
            if not self._shutting_down:
                await self._cancel(record)
        except MCPError as exc:
            if not self._shutting_down:
                await self._fail(
                    record,
                    code=exc.code,
                    message=exc.message,
                    data=exc.data,
                )
        except FastMCPError as exc:
            if not self._shutting_down:
                await self._complete(record, _tool_error_payload(exc))
        except Exception as exc:
            if not self._shutting_down:
                logger.exception("Background MCP task %s failed", record.task_id)
                await self._fail(
                    record,
                    code=mcp_types.INTERNAL_ERROR,
                    message=str(exc) or type(exc).__name__,
                )
        finally:
            self._started_at.pop(record.task_id, None)

    async def _complete(
        self,
        record: StoredTask,
        result: dict[str, Any],
    ) -> TaskStatus:
        return await self._write_terminal(
            record,
            status="completed",
            result=result,
        )

    async def _fail(
        self,
        record: StoredTask,
        *,
        code: int,
        message: str,
        data: Any = None,
    ) -> TaskStatus:
        return await self._write_terminal(
            record,
            status="failed",
            status_message=message,
            error=mcp_types.ErrorData(code=code, message=message, data=data),
        )

    async def _cancel(self, record: StoredTask) -> TaskStatus:
        return await self._write_terminal(record, status="cancelled")

    async def _write_terminal(
        self,
        record: StoredTask,
        *,
        status: TaskStatus,
        status_message: str | None = None,
        result: dict[str, Any] | None = None,
        error: mcp_types.ErrorData | None = None,
    ) -> TaskStatus:
        updated_at = now_iso()
        terminal = GetTaskResult(
            task_id=record.task_id,
            status=status,
            created_at=record.created_at,
            last_updated_at=updated_at,
            ttl_ms=record.ttl_ms,
            status_message=status_message,
            poll_interval_ms=record.poll_interval_ms,
            result=result,
            error=error,
        )
        terminal_json = terminal.model_dump_json(
            by_alias=True,
            exclude_none=True,
        ).encode("utf-8")
        try:
            changed, evicted = await self._store.set_terminal(
                record.task_id,
                status=status,
                updated_at=updated_at,
                terminal_json=terminal_json,
            )
        except TaskCapacityError:
            is_storage_failure = (
                status == "failed"
                and error is not None
                and error.code == _TASK_STORAGE_ERROR
            )
            if is_storage_failure:
                raise
            message = "Task result exceeds the task state byte budget"
            return await self._write_terminal(
                record,
                status="failed",
                status_message=message,
                error=mcp_types.ErrorData(
                    code=_TASK_STORAGE_ERROR,
                    message=message,
                ),
            )

        self._cancel_runners(evicted)
        if changed:
            started_at = self._started_at.get(record.task_id)
            duration = (
                max(0.0, time.monotonic() - started_at)
                if started_at is not None
                else 0.0
            )
            nomad_metrics.record_task_outcome(status, duration)
            return status
        current = await self._store.get_any(record.task_id)
        return current.status if current is not None else status

    def _create_result(self, record: StoredTask) -> CreateTaskResult:
        return CreateTaskResult(
            task_id=record.task_id,
            status=record.status,
            created_at=record.created_at,
            last_updated_at=record.updated_at,
            ttl_ms=record.ttl_ms,
            poll_interval_ms=record.poll_interval_ms,
        )

    async def _lookup(self, task_id: str) -> StoredTask:
        record = await self._store.get(task_id, task_owner_key())
        if record is None:
            raise _task_not_found(task_id)
        if time.time() >= record.expires_at:
            await self._store.delete(task_id, reason="expired")
            self._cancel_runners((task_id,))
            raise _task_not_found(task_id)
        return record

    @staticmethod
    def _to_result(record: StoredTask) -> GetTaskResult:
        if record.terminal_json is not None:
            return GetTaskResult.model_validate_json(record.terminal_json)
        if record.status != "working":
            raise MCPError(
                code=mcp_types.INTERNAL_ERROR,
                message=f"Task {record.task_id} has no terminal state",
            )
        return GetTaskResult(
            task_id=record.task_id,
            status="working",
            created_at=record.created_at,
            last_updated_at=record.updated_at,
            ttl_ms=record.ttl_ms,
            poll_interval_ms=record.poll_interval_ms,
        )

    def _check_task_request(
        self,
        ctx: ServerRequestContext[Any, Any],
        task_id: str,
    ) -> None:
        if read_client_extension_settings(ctx, TASKS_EXTENSION_ID) is None:
            raise MCPError(
                code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                message="The client did not declare the tasks extension.",
                data=_missing_capability_data(),
            )
        try:
            request = get_http_request()
        except RuntimeError:
            return
        if decode_header_value(request.headers.get(MCP_NAME_HEADER)) != task_id:
            raise MCPError(
                code=HEADER_MISMATCH,
                message=(
                    f"{MCP_NAME_HEADER} header does not match the request body's "
                    "'taskId' parameter"
                ),
            )

    async def _handle_get(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: GetTaskParams,
    ) -> GetTaskResult:
        self._check_task_request(ctx, params.task_id)
        nomad_metrics.record_task_request("get")
        return self._to_result(await self._lookup(params.task_id))

    async def _handle_update(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: UpdateTaskParams,
    ) -> UpdateTaskResult:
        self._check_task_request(ctx, params.task_id)
        nomad_metrics.record_task_request("update")
        await self._lookup(params.task_id)
        # Managed model tools never enter input_required. Unknown or already-
        # satisfied response keys are ignored as required by SEP-2663.
        return UpdateTaskResult()

    async def _handle_cancel(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: CancelTaskParams,
    ) -> CancelTaskResult:
        self._check_task_request(ctx, params.task_id)
        nomad_metrics.record_task_request("cancel")
        record = await self._lookup(params.task_id)
        if record.status == "working":
            await self._cancel(record)
            self._cancel_runners((record.task_id,))
        return CancelTaskResult()

    def _cancel_runners(self, task_ids: Sequence[str]) -> None:
        for task_id in task_ids:
            runner = self._runners.get(task_id)
            if runner is not None and not runner.done():
                runner.cancel()

    async def _prune_expired(self) -> None:
        expired = await self._store.prune_expired()
        self._cancel_runners(expired)

    async def _janitor_loop(self) -> None:
        interval = min(60.0, max(0.05, self._ttl_seconds / 2))
        while True:
            await asyncio.sleep(interval)
            await self._prune_expired()

    async def _recover_interrupted(self) -> None:
        for record in await self._store.list_working():
            await self._fail(
                record,
                code=mcp_types.INTERNAL_ERROR,
                message="Server restarted before task execution completed",
            )

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        await self._prune_expired()
        await self._recover_interrupted()
        self._janitor = asyncio.create_task(
            self._janitor_loop(),
            name="nomad-mcp-task-janitor",
        )
        try:
            yield
        finally:
            self._shutting_down = True
            if self._janitor is not None:
                self._janitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._janitor
                self._janitor = None

            for record in await self._store.list_working():
                await self._cancel(record)
            runners = tuple(
                runner for runner in self._runners.values() if not runner.done()
            )
            for runner in runners:
                runner.cancel()
            if runners:
                _, pending = await asyncio.wait(
                    runners,
                    timeout=self._shutdown_timeout_seconds,
                )
                if pending:
                    logger.warning(
                        "%d MCP task runner(s) did not stop within %.3f seconds: %s",
                        len(pending),
                        self._shutdown_timeout_seconds,
                        ", ".join(sorted(runner.get_name() for runner in pending)),
                    )
                    for runner in pending:
                        runner.cancel()
            await self._store.close()


__all__ = [
    "CancelTaskParams",
    "CancelTaskRequest",
    "CancelTaskResult",
    "CreateTaskResult",
    "GetTaskParams",
    "GetTaskRequest",
    "GetTaskResult",
    "NomadTasksClientExtension",
    "NomadTasksExtension",
    "TaskStatus",
    "UpdateTaskParams",
    "UpdateTaskRequest",
    "UpdateTaskResult",
]
