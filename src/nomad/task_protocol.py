"""SEP-2663 task wire models for the stable 2026-07-28 protocol."""

from __future__ import annotations

from typing import Any, Literal

import mcp_types
from mcp_types import Request, RequestParams, Result
from pydantic import ConfigDict, Field, model_validator

TASKS_PROTOCOL_VERSION = "2026-07-28"
TASKS_PROTOCOL_VERSIONS = frozenset({TASKS_PROTOCOL_VERSION})
TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})
_MIN_SAFE_INTEGER = -(2**53) + 1
_MAX_SAFE_INTEGER = 2**53 - 1


class _TaskFields(Result):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    status: TaskStatus
    created_at: str = Field(alias="createdAt")
    last_updated_at: str = Field(alias="lastUpdatedAt")
    ttl_ms: int | None = Field(
        alias="ttlMs",
        ge=_MIN_SAFE_INTEGER,
        le=_MAX_SAFE_INTEGER,
    )
    status_message: str | None = Field(default=None, alias="statusMessage")
    poll_interval_ms: int | None = Field(
        default=None,
        alias="pollIntervalMs",
        ge=_MIN_SAFE_INTEGER,
        le=_MAX_SAFE_INTEGER,
    )


class CreateTaskResult(_TaskFields):
    """Task handle returned instead of an inline result."""

    result_type: Literal["task"] = Field(default="task", alias="resultType")


class GetTaskResult(_TaskFields):
    """Status-specific task state returned by ``tasks/get``."""

    result_type: Literal["complete"] = Field(default="complete", alias="resultType")
    result: dict[str, Any] | None = None
    error: mcp_types.ErrorData | None = None
    input_requests: mcp_types.InputRequests | None = Field(
        default=None,
        alias="inputRequests",
    )

    @model_validator(mode="after")
    def validate_status_payload(self) -> GetTaskResult:
        if self.status == "completed" and self.result is None:
            raise ValueError("completed tasks require a result")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed tasks require an error")
        if self.status == "input_required" and self.input_requests is None:
            raise ValueError("input_required tasks require inputRequests")
        if self.status != "completed" and self.result is not None:
            raise ValueError("only completed tasks may include a result")
        if self.status != "failed" and self.error is not None:
            raise ValueError("only failed tasks may include an error")
        if self.status != "input_required" and self.input_requests is not None:
            raise ValueError("only input_required tasks may include inputRequests")
        return self


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
    input_responses: mcp_types.InputResponses = Field(alias="inputResponses")


class GetTaskRequest(Request[GetTaskParams, Literal["tasks/get"]]):
    method: Literal["tasks/get"] = "tasks/get"
    params: GetTaskParams
    # The Python SDK copies this parameter to the Mcp-Name routing header.
    name_param = "taskId"


class UpdateTaskRequest(Request[UpdateTaskParams, Literal["tasks/update"]]):
    method: Literal["tasks/update"] = "tasks/update"
    params: UpdateTaskParams
    name_param = "taskId"


class CancelTaskRequest(Request[CancelTaskParams, Literal["tasks/cancel"]]):
    method: Literal["tasks/cancel"] = "tasks/cancel"
    params: CancelTaskParams
    name_param = "taskId"
