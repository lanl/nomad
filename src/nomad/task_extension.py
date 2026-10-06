"""In-process implementation of the SEP-2663 MCP Tasks extension."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import mcp_types
from fastmcp.exceptions import FastMCPError, NotFoundError
from fastmcp.server.dependencies import (
    extract_version_spec,
    get_access_token,
    get_http_request,
)
from fastmcp.server.extensions import (
    MethodBinding,
    ServerExtension,
    read_client_extension_settings,
)
from fastmcp.tools.base import InputRequiredToolResult, ToolResult
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
from fastmcp.utilities.versions import VersionSpec
from mcp.client.extension import ClaimContext, ClientExtension, ResultClaim
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import MCP_NAME_HEADER, decode_header_value
from mcp_types import Request, RequestParams, Result
from mcp_types.jsonrpc import (
    HEADER_MISMATCH,
    MISSING_REQUIRED_CLIENT_CAPABILITY,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import ConfigDict, Field

if TYPE_CHECKING:
    from fastmcp.server.context import Context
    from fastmcp.server.extensions import ToolCallContinuation, ToolCallOutcome

logger = logging.getLogger(__name__)

TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]
_TASK_METHOD_VERSIONS = frozenset(MODERN_PROTOCOL_VERSIONS)
_MIN_POLL_SECONDS = 0.02
_SERVER_BUSY = -32000


class _TaskFields(Result):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    status: TaskStatus
    created_at: str = Field(alias="createdAt")
    last_updated_at: str = Field(alias="lastUpdatedAt")
    ttl_ms: int | None = Field(alias="ttlMs")
    status_message: str | None = Field(default=None, alias="statusMessage")
    poll_interval_ms: int | None = Field(default=None, alias="pollIntervalMs")


class CreateTaskResult(_TaskFields):
    """Task handle returned instead of an inline ``tools/call`` result."""

    result_type: Literal["task"] = Field(default="task", alias="resultType")


class GetTaskResult(_TaskFields):
    """Current state returned by ``tasks/get``."""

    result_type: Literal["complete"] = Field(default="complete", alias="resultType")
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    input_requests: dict[str, Any] | None = Field(
        default=None,
        alias="inputRequests",
    )


class UpdateTaskResult(Result):
    result_type: Literal["complete"] = Field(default="complete", alias="resultType")


class CancelTaskResult(Result):
    result_type: Literal["complete"] = Field(default="complete", alias="resultType")


class GetTaskParams(RequestParams):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")


CancelTaskParams = GetTaskParams


class UpdateTaskParams(RequestParams):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    input_responses: dict[str, Any] = Field(alias="inputResponses")


class GetTaskRequest(Request[GetTaskParams, Literal["tasks/get"]]):
    method: Literal["tasks/get"] = "tasks/get"
    params: GetTaskParams
    name_param = "taskId"


class UpdateTaskRequest(Request[UpdateTaskParams, Literal["tasks/update"]]):
    method: Literal["tasks/update"] = "tasks/update"
    params: UpdateTaskParams
    name_param = "taskId"


class CancelTaskRequest(Request[CancelTaskParams, Literal["tasks/cancel"]]):
    method: Literal["tasks/cancel"] = "tasks/cancel"
    params: CancelTaskParams
    name_param = "taskId"


@dataclass(slots=True)
class _TaskRecord:
    task_id: str
    owner: tuple[str | None, str, str | None, str | None] | None
    created_at: str
    updated_at: str
    created_monotonic: float
    ttl_ms: int
    poll_interval_ms: int
    status: TaskStatus = "working"
    status_message: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    runner: asyncio.Task[None] | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _task_owner() -> tuple[str | None, str, str | None, str | None] | None:
    token = get_access_token()
    if token is None:
        return None
    claims = token.claims or {}
    issuer = claims.get("iss")
    subject = token.subject or claims.get("sub")
    return (
        str(issuer) if issuer is not None else None,
        token.client_id,
        str(subject) if subject is not None else None,
        token.resource,
    )


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


class NomadTasksExtension(ServerExtension):
    """Run the named model tools as tasks inside this server process.

    The explicit name boundary keeps tools that may use multi-round-trip
    ``input_required`` results on FastMCP's synchronous execution path.
    """

    identifier = TASKS_EXTENSION_ID

    def __init__(
        self,
        *,
        tool_names: Collection[str],
        ttl_seconds: float = 15 * 60,
        max_records: int = 2**16,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        if max_records < 1:
            raise ValueError("max_records must be at least one")
        if shutdown_timeout_seconds < 0:
            raise ValueError("shutdown_timeout_seconds cannot be negative")
        self._tool_names = frozenset(tool_names)
        self._ttl_seconds = ttl_seconds
        self._ttl_ms = max(1, int(ttl_seconds * 1000))
        self._max_records = max_records
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._records: dict[str, _TaskRecord] = {}
        self._runners: set[asyncio.Task[None]] = set()
        self._janitor: asyncio.Task[None] | None = None

    def settings(self) -> dict[str, Any]:
        return {}

    def methods(self) -> Sequence[MethodBinding]:
        return (
            MethodBinding(
                method="tasks/get",
                params_type=GetTaskParams,
                handler=self._handle_get,
                protocol_versions=_TASK_METHOD_VERSIONS,
            ),
            MethodBinding(
                method="tasks/update",
                params_type=UpdateTaskParams,
                handler=self._handle_update,
                protocol_versions=_TASK_METHOD_VERSIONS,
            ),
            MethodBinding(
                method="tasks/cancel",
                params_type=CancelTaskParams,
                handler=self._handle_cancel,
                protocol_versions=_TASK_METHOD_VERSIONS,
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
        if tool is None or not tool.task_config.supports_tasks():
            return await call_next()

        request_context = context.request_context
        opted_in = (
            request_context is not None
            and request_context.protocol_version in MODERN_PROTOCOL_VERSIONS
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
        if opted_in and mode in {"optional", "required"}:
            poll_interval = max(
                _MIN_POLL_SECONDS,
                tool.task_config.poll_interval.total_seconds(),
            )
            return self._create_task(call_next, poll_interval=poll_interval)
        return await call_next()

    def _create_task(
        self,
        call_next: ToolCallContinuation,
        *,
        poll_interval: float,
    ) -> CreateTaskResult:
        self._prune_expired()
        if len(self._records) >= self._max_records:
            raise MCPError(
                code=_SERVER_BUSY,
                message="Server busy: too many retained tasks",
            )

        task_id = secrets.token_urlsafe(32)
        created_at = _now_iso()
        record = _TaskRecord(
            task_id=task_id,
            owner=_task_owner(),
            created_at=created_at,
            updated_at=created_at,
            created_monotonic=time.monotonic(),
            ttl_ms=self._ttl_ms,
            poll_interval_ms=int(poll_interval * 1000),
        )
        self._records[task_id] = record
        runner = asyncio.create_task(
            self._execute(record, call_next),
            name=f"nomad-mcp-task-{task_id}",
        )
        record.runner = runner
        self._runners.add(runner)
        runner.add_done_callback(self._runners.discard)
        return self._create_result(record)

    async def _execute(
        self,
        record: _TaskRecord,
        call_next: ToolCallContinuation,
    ) -> None:
        try:
            outcome = await call_next()
            if isinstance(outcome, InputRequiredToolResult):
                raise RuntimeError(
                    "Task-backed tools do not support input-required results"
                )
            if not isinstance(outcome, ToolResult):
                raise RuntimeError(
                    f"Task returned unsupported result {type(outcome).__name__}"
                )
            if record.status != "cancelled":
                record.result = _tool_result_payload(outcome)
                self._set_status(record, "completed")
        except asyncio.CancelledError:
            if record.status == "working":
                self._set_status(record, "cancelled")
        except MCPError as exc:
            self._fail_task(
                record,
                code=exc.code,
                message=exc.message,
                data=exc.data,
            )
        except FastMCPError as exc:
            if record.status != "cancelled":
                record.result = _tool_error_payload(exc)
                self._set_status(record, "completed")
        except Exception as exc:
            logger.exception("Background MCP task %s failed", record.task_id)
            self._fail_task(
                record,
                code=mcp_types.INTERNAL_ERROR,
                message=str(exc) or type(exc).__name__,
            )

    def _set_status(self, record: _TaskRecord, status: TaskStatus) -> None:
        record.status = status
        record.updated_at = _now_iso()

    def _fail_task(
        self,
        record: _TaskRecord,
        *,
        code: int,
        message: str,
        data: Any = None,
    ) -> None:
        if record.status == "cancelled":
            return
        record.status_message = message
        record.error = {"code": code, "message": message}
        if data is not None:
            record.error["data"] = data
        self._set_status(record, "failed")

    def _create_result(self, record: _TaskRecord) -> CreateTaskResult:
        return CreateTaskResult(
            task_id=record.task_id,
            status=record.status,
            created_at=record.created_at,
            last_updated_at=record.updated_at,
            ttl_ms=record.ttl_ms,
            poll_interval_ms=record.poll_interval_ms,
        )

    def _get_result(self, record: _TaskRecord) -> GetTaskResult:
        return GetTaskResult(
            task_id=record.task_id,
            status=record.status,
            created_at=record.created_at,
            last_updated_at=record.updated_at,
            ttl_ms=record.ttl_ms,
            status_message=record.status_message,
            poll_interval_ms=record.poll_interval_ms,
            result=record.result,
            error=record.error,
        )

    def _lookup(self, task_id: str) -> _TaskRecord:
        record = self._records.get(task_id)
        if record is None or record.owner != _task_owner():
            raise _task_not_found(task_id)
        if self._is_expired(record):
            self._discard(record)
            raise _task_not_found(task_id)
        return record

    def _is_expired(self, record: _TaskRecord) -> bool:
        return time.monotonic() - record.created_monotonic >= self._ttl_seconds

    def _discard(self, record: _TaskRecord) -> None:
        self._records.pop(record.task_id, None)
        if record.runner is not None and not record.runner.done():
            record.runner.cancel()

    def _prune_expired(self) -> None:
        for record in tuple(self._records.values()):
            if self._is_expired(record):
                self._discard(record)

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
        return self._get_result(self._lookup(params.task_id))

    async def _handle_update(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: UpdateTaskParams,
    ) -> UpdateTaskResult:
        self._check_task_request(ctx, params.task_id)
        self._lookup(params.task_id)
        # Nomad model tools never enter input_required. SEP-2663 requires unknown
        # or already-satisfied response keys to be ignored, so this is a no-op.
        return UpdateTaskResult()

    async def _handle_cancel(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: CancelTaskParams,
    ) -> CancelTaskResult:
        self._check_task_request(ctx, params.task_id)
        record = self._lookup(params.task_id)
        if record.status == "working":
            self._set_status(record, "cancelled")
            if record.runner is not None:
                record.runner.cancel()
        return CancelTaskResult()

    async def _janitor_loop(self) -> None:
        interval = min(60.0, max(0.05, self._ttl_seconds / 2))
        while True:
            await asyncio.sleep(interval)
            self._prune_expired()

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        self._janitor = asyncio.create_task(
            self._janitor_loop(),
            name="nomad-mcp-task-janitor",
        )
        try:
            yield
        finally:
            if self._janitor is not None:
                self._janitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._janitor
                self._janitor = None
            runners = tuple(runner for runner in self._runners if not runner.done())
            for runner in runners:
                runner.cancel()
            if runners:
                _, pending = await asyncio.wait(
                    runners,
                    timeout=self._shutdown_timeout_seconds,
                )
                if pending:
                    logger.warning(
                        ("%d MCP task runner(s) did not stop within %.3f seconds: %s"),
                        len(pending),
                        self._shutdown_timeout_seconds,
                        ", ".join(sorted(runner.get_name() for runner in pending)),
                    )
                    for runner in pending:
                        runner.cancel()
            self._records.clear()


class NomadTasksClientExtension(ClientExtension):
    """Transparent Python client support for Nomad's task extension."""

    identifier = TASKS_EXTENSION_ID

    def claims(self) -> Sequence[ResultClaim[Any]]:
        return (
            ResultClaim(
                result_type="task",
                model=CreateTaskResult,
                resolve=_resolve_task,
                protocol_versions=_TASK_METHOD_VERSIONS,
            ),
        )


async def _resolve_task(
    created: CreateTaskResult,
    context: ClaimContext,
) -> mcp_types.CallToolResult:
    deadline = (
        time.monotonic() + context.read_timeout_seconds
        if context.read_timeout_seconds is not None
        else None
    )
    while True:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise TimeoutError(f"Task {created.task_id} did not complete in time")
        result = await context.session.send_request(
            GetTaskRequest(params=GetTaskParams(task_id=created.task_id)),
            GetTaskResult,
            request_read_timeout_seconds=remaining,
        )
        if result.status == "completed":
            if result.result is None:
                raise MCPError(
                    code=mcp_types.INTERNAL_ERROR,
                    message=f"Task {created.task_id} completed without a result",
                )
            return mcp_types.CallToolResult.model_validate(result.result)
        if result.status == "failed":
            error = result.error or {
                "code": mcp_types.INTERNAL_ERROR,
                "message": result.status_message or "Task failed",
            }
            raise MCPError(
                code=int(error.get("code", mcp_types.INTERNAL_ERROR)),
                message=str(error.get("message", "Task failed")),
                data=error.get("data"),
            )
        if result.status == "cancelled":
            raise MCPError(
                code=_SERVER_BUSY, message=f"Task {created.task_id} cancelled"
            )
        if result.status == "input_required":
            raise MCPError(
                code=mcp_types.INTERNAL_ERROR,
                message="Nomad tasks do not support input-required execution",
            )
        delay = max(
            _MIN_POLL_SECONDS,
            (result.poll_interval_ms or created.poll_interval_ms or 1000) / 1000,
        )
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Task {created.task_id} did not complete in time")
            await asyncio.sleep(min(delay, remaining))
        else:
            await asyncio.sleep(delay)
