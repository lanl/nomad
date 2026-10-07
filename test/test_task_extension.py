from __future__ import annotations

import asyncio
import json
import socket
import time
import types
from datetime import timedelta

import mcp_types
import pytest
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.utilities.tasks import TaskConfig
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import HEADER_MISMATCH, MISSING_REQUIRED_CLIENT_CAPABILITY
from starlette.middleware import Middleware

from nomad import task_client as task_client_module
from nomad import task_extension as task_module
from nomad import task_store as task_store_module
from nomad.task_client import (
    TaskCancelledError,
    TaskTimeoutError,
    resolve_task,
)
from nomad.task_extension import (
    CancelTaskParams,
    CancelTaskRequest,
    CancelTaskResult,
    CreateTaskResult,
    GetTaskParams,
    GetTaskRequest,
    GetTaskResult,
    NomadTasksClientExtension,
    NomadTasksExtension,
    UpdateTaskParams,
    UpdateTaskRequest,
    UpdateTaskResult,
)
from nomad.task_store import task_owner_key


async def _get_task(client: Client, task_id: str) -> GetTaskResult:
    return await client.session.send_request(
        GetTaskRequest(params=GetTaskParams(task_id=task_id)),
        GetTaskResult,
    )


async def _wait_for_terminal(client: Client, task_id: str) -> GetTaskResult:
    async def poll() -> GetTaskResult:
        while True:
            result = await _get_task(client, task_id)
            if result.status != "working":
                return result
            await asyncio.sleep(0)

    return await asyncio.wait_for(poll(), timeout=1)


class CaptureMcpNameHeader:
    def __init__(self, app, *, values: list[str]) -> None:
        self.app = app
        self.values = values

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            value = headers.get(b"mcp-name")
            if value is not None:
                self.values.append(value.decode("ascii"))
        await self.app(scope, receive, send)


@pytest.mark.asyncio()
async def test_task_handle_can_be_polled_updated_and_cancelled():
    server = FastMCP("task-lifecycle")
    server.add_extension(NomadTasksExtension(tool_names={"wait_for_release"}))
    release = asyncio.Event()

    @server.tool(task=True)
    async def wait_for_release() -> str:
        await release.wait()
        return "released"

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        created = await client.session.call_tool(
            "wait_for_release",
            {},
            allow_claimed=True,
        )
        assert isinstance(created, CreateTaskResult)
        assert (await _get_task(client, created.task_id)).status == "working"

        await client.session.send_request(
            UpdateTaskRequest(
                params=UpdateTaskParams(
                    task_id=created.task_id,
                    input_responses={
                        "unused": mcp_types.ElicitResult(action="decline")
                    },
                )
            ),
            UpdateTaskResult,
        )
        await client.session.send_request(
            CancelTaskRequest(params=CancelTaskParams(task_id=created.task_id)),
            CancelTaskResult,
        )

        assert (await _get_task(client, created.task_id)).status == "cancelled"


@pytest.mark.asyncio()
async def test_task_client_transparently_resolves_completed_result():
    server = FastMCP("task-result")
    server.add_extension(NomadTasksExtension(tool_names={"add"}))

    @server.tool(
        task=TaskConfig(
            mode="optional",
            poll_interval=timedelta(milliseconds=20),
        )
    )
    async def add(left: int, right: int) -> int:
        await asyncio.sleep(0)
        return left + right

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        result = await client.call_tool("add", {"left": 20, "right": 22})

    assert result.structured_content == {"result": 42}


@pytest.mark.asyncio()
async def test_synchronous_call_uses_task_store_and_deletes_after_delivery():
    extension = NomadTasksExtension(tool_names={"blocked_add"})
    server = FastMCP("stored-inline-result")
    server.add_extension(extension)
    started = asyncio.Event()
    release = asyncio.Event()

    @server.tool(task=True)
    async def blocked_add(left: int, right: int) -> int:
        started.set()
        await release.wait()
        return left + right

    async with Client(server._mcp_server) as client:
        call = asyncio.create_task(
            client.call_tool("blocked_add", {"left": 20, "right": 22})
        )
        await started.wait()

        records = await extension._store.list_working()
        assert len(records) == 1
        assert json.loads(records[0].input_json) == {
            "method": "tools/call",
            "params": {
                "arguments": {"left": 20, "right": 22},
                "name": "blocked_add",
            },
        }

        release.set()
        result = await call
        assert result.structured_content == {"result": 42}
        assert await extension._store.list_tasks() == ()


@pytest.mark.asyncio()
async def test_restart_marks_interrupted_persisted_task_failed(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    original = task_store_module.SQLiteTaskStore(
        path,
        ttl_seconds=60,
        max_bytes=4096,
    )
    await original.create(
        task_id="interrupted",
        owner_key=task_owner_key(),
        method="tools/call",
        input_json=b'{"method":"tools/call","params":{"name":"model"}}',
        poll_interval_ms=20,
    )
    await original.close()

    extension = NomadTasksExtension(
        tool_names=set(),
        store_path=path,
        ttl_seconds=60,
        max_bytes=4096,
    )
    server = FastMCP("task-recovery")
    server.add_extension(extension)

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        recovered = await _get_task(client, "interrupted")

    assert recovered.status == "failed"
    assert recovered.error is not None
    assert recovered.error.code == mcp_types.INTERNAL_ERROR
    assert recovered.error.message == "Server restarted before task execution completed"


@pytest.mark.asyncio()
async def test_tool_failure_completes_with_is_error_result():
    server = FastMCP("task-failure")
    server.add_extension(NomadTasksExtension(tool_names={"fail"}))

    @server.tool(task=True)
    async def fail() -> None:
        raise RuntimeError("deliberate failure")

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        created = await client.session.call_tool("fail", {}, allow_claimed=True)
        assert isinstance(created, CreateTaskResult)
        result = await _wait_for_terminal(client, created.task_id)
        transparent = await client.call_tool("fail", {})

    assert result.status == "completed"
    assert result.result is not None
    assert result.result["isError"] is True
    assert result.result["content"][0]["text"].endswith("deliberate failure")
    assert transparent.is_error is True
    assert transparent.content[0].text.endswith("deliberate failure")


@pytest.mark.asyncio()
async def test_json_rpc_failure_uses_failed_task_status():
    server = FastMCP("task-protocol-failure")
    server.add_extension(NomadTasksExtension(tool_names={"fail_protocol"}))

    @server.tool(task=True)
    async def fail_protocol() -> None:
        raise MCPError(
            code=MISSING_REQUIRED_CLIENT_CAPABILITY,
            message="missing required capability",
        )

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        created = await client.session.call_tool(
            "fail_protocol", {}, allow_claimed=True
        )
        assert isinstance(created, CreateTaskResult)
        result = await _wait_for_terminal(client, created.task_id)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.model_dump(exclude_none=True) == {
        "code": MISSING_REQUIRED_CLIENT_CAPABILITY,
        "message": "missing required capability",
    }


@pytest.mark.asyncio()
async def test_task_capacity_is_bounded():
    server = FastMCP("task-capacity")
    server.add_extension(NomadTasksExtension(tool_names={"blocked"}, max_bytes=2048))
    release = asyncio.Event()

    @server.tool(task=True)
    async def blocked() -> None:
        await release.wait()

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        created = await client.session.call_tool("blocked", {}, allow_claimed=True)
        assert isinstance(created, CreateTaskResult)

        with pytest.raises(MCPError, match="byte budget exhausted"):
            await client.session.call_tool("blocked", {}, allow_claimed=True)

        await client.session.send_request(
            CancelTaskRequest(params=CancelTaskParams(task_id=created.task_id)),
            CancelTaskResult,
        )


@pytest.mark.asyncio()
async def test_expired_task_is_not_found():
    server = FastMCP("task-expiration")
    server.add_extension(NomadTasksExtension(tool_names={"finish"}, ttl_seconds=0.01))

    @server.tool(task=True)
    async def finish() -> str:
        return "done"

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        created = await client.session.call_tool("finish", {}, allow_claimed=True)
        assert isinstance(created, CreateTaskResult)
        await asyncio.sleep(0.02)

        with pytest.raises(MCPError, match="not found"):
            await _get_task(client, created.task_id)


@pytest.mark.asyncio()
async def test_task_client_uses_one_total_polling_timeout():
    server = FastMCP("task-timeout")
    server.add_extension(NomadTasksExtension(tool_names={"slow"}))

    @server.tool(
        task=TaskConfig(
            mode="optional",
            poll_interval=timedelta(milliseconds=20),
        )
    )
    async def slow() -> str:
        await asyncio.sleep(0.2)
        return "late"

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        started = time.monotonic()
        with pytest.raises(TaskTimeoutError) as exc_info:
            await client.call_tool("slow", {}, read_timeout_seconds=0.05)

        assert time.monotonic() - started < 0.15
        assert exc_info.value.task_id


@pytest.mark.asyncio()
async def test_task_client_normalizes_sdk_request_timeout_with_task_id():
    created = CreateTaskResult(
        task_id="recoverable-task",
        status="working",
        created_at="2026-07-28T00:00:00Z",
        last_updated_at="2026-07-28T00:00:00Z",
        ttl_ms=60_000,
    )

    class TimedOutSession:
        async def send_request(self, *args, **kwargs):
            raise MCPError(code=mcp_types.REQUEST_TIMEOUT, message="timed out")

    with pytest.raises(TaskTimeoutError) as exc_info:
        await resolve_task(
            created,
            TimedOutSession(),
            read_timeout_seconds=1,
        )

    assert exc_info.value.task_id == "recoverable-task"


@pytest.mark.asyncio()
async def test_task_client_respects_initial_poll_interval(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay: float):
        sleeps.append(delay)

    monkeypatch.setattr(task_client_module.asyncio, "sleep", fake_sleep)
    created = CreateTaskResult(
        task_id="scheduled-task",
        status="working",
        created_at="2026-07-28T00:00:00Z",
        last_updated_at="2026-07-28T00:00:00Z",
        ttl_ms=60_000,
        poll_interval_ms=250,
    )

    class CompletedSession:
        async def send_request(self, *args, **kwargs):
            return GetTaskResult(
                task_id="scheduled-task",
                status="completed",
                created_at="2026-07-28T00:00:00Z",
                last_updated_at="2026-07-28T00:00:01Z",
                ttl_ms=60_000,
                result={"content": []},
            )

    await resolve_task(
        created,
        CompletedSession(),
        read_timeout_seconds=None,
    )

    assert sleeps == [0.25]


@pytest.mark.asyncio()
async def test_cancelling_client_polling_requests_remote_task_cancellation():
    extension = NomadTasksExtension(tool_names={"blocked"})
    server = FastMCP("task-client-cancellation")
    server.add_extension(extension)
    started = asyncio.Event()
    release = asyncio.Event()

    @server.tool(task=True)
    async def blocked() -> str:
        started.set()
        await release.wait()
        return "released"

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        call = asyncio.create_task(client.call_tool("blocked", {}))
        await started.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

        assert {record.status for record in await extension._store.list_tasks()} == {
            "cancelled"
        }


@pytest.mark.asyncio()
async def test_cancelled_terminal_state_has_a_distinct_client_error():
    server = FastMCP("task-cancelled-error")
    server.add_extension(NomadTasksExtension(tool_names={"blocked"}))
    release = asyncio.Event()

    @server.tool(
        task=TaskConfig(
            mode="optional",
            poll_interval=timedelta(milliseconds=20),
        )
    )
    async def blocked() -> str:
        await release.wait()
        return "released"

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        created = await client.session.call_tool("blocked", {}, allow_claimed=True)
        assert isinstance(created, CreateTaskResult)
        await client.session.send_request(
            CancelTaskRequest(params=CancelTaskParams(task_id=created.task_id)),
            CancelTaskResult,
        )
        with pytest.raises(TaskCancelledError) as exc_info:
            await resolve_task(
                created,
                client.session,
                read_timeout_seconds=1,
            )

    assert exc_info.value.task_id == created.task_id


@pytest.mark.asyncio()
async def test_expired_runner_remains_tracked_and_shutdown_is_bounded():
    extension = NomadTasksExtension(
        tool_names={"stubborn"},
        ttl_seconds=0.01,
        shutdown_timeout_seconds=0.01,
    )
    server = FastMCP("task-runner-lifecycle")
    server.add_extension(extension)
    started = asyncio.Event()
    release = asyncio.Event()

    @server.tool(task=True)
    async def stubborn() -> str:
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return "released"

    shutdown_started = 0.0
    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        created = await client.session.call_tool("stubborn", {}, allow_claimed=True)
        assert isinstance(created, CreateTaskResult)
        await started.wait()
        await asyncio.sleep(0.02)
        with pytest.raises(MCPError, match="not found"):
            await _get_task(client, created.task_id)
        assert extension._runners
        shutdown_started = time.monotonic()

    assert time.monotonic() - shutdown_started < 0.15
    runners = tuple(extension._runners.values())
    assert runners
    release.set()
    await asyncio.gather(*runners)


@pytest.mark.asyncio()
async def test_non_model_task_enabled_tool_is_not_intercepted():
    server = FastMCP("task-scope")
    server.add_extension(NomadTasksExtension(tool_names={"model_tool"}))

    @server.tool(task=True)
    async def other_tool() -> str:
        return "inline"

    async with Client(
        server._mcp_server,
        extensions=[NomadTasksClientExtension()],
    ) as client:
        result = await client.session.call_tool("other_tool", {}, allow_claimed=True)

    assert not isinstance(result, CreateTaskResult)
    assert result.structured_content == {"result": "inline"}


@pytest.mark.asyncio()
async def test_task_owner_uses_canonical_subject_and_prevents_cross_user_lookup(
    monkeypatch,
):
    current_token = AccessToken(
        token="alice-token",
        client_id="shared-client",
        scopes=[],
        resource="nomad",
        subject="alice",
        claims={"iss": "https://issuer.example"},
    )
    monkeypatch.setattr(
        task_store_module,
        "get_access_token",
        lambda: current_token,
    )
    owner_key = task_owner_key()
    current_token = current_token.model_copy(update={"token": "rotated-alice-token"})
    assert task_owner_key() == owner_key
    extension = NomadTasksExtension(tool_names={"model"})
    await extension._store.create(
        task_id="secret",
        owner_key=owner_key,
        method="tools/call",
        input_json=b"{}",
        poll_interval_ms=20,
    )

    current_token = current_token.model_copy(
        update={"token": "bob-token", "subject": "bob"}
    )

    with pytest.raises(MCPError, match="not found"):
        await extension._lookup("secret")
    await extension._store.close()


@pytest.mark.parametrize("header", [None, "wrong", "=?base64?not-valid?="])
def test_http_task_request_requires_matching_mcp_name_header(monkeypatch, header):
    extension = NomadTasksExtension(tool_names={"model"})
    headers = {} if header is None else {"mcp-name": header}
    monkeypatch.setattr(
        task_module,
        "read_client_extension_settings",
        lambda ctx, identifier: {},
    )
    monkeypatch.setattr(
        task_module,
        "get_http_request",
        lambda: types.SimpleNamespace(headers=headers),
    )

    with pytest.raises(MCPError) as exc_info:
        extension._check_task_request(object(), "expected")

    assert exc_info.value.code == HEADER_MISMATCH


def test_sep2663_models_use_the_stable_flat_wire_shape():
    created = CreateTaskResult(
        task_id="task-1",
        status="working",
        created_at="2026-07-28T00:00:00Z",
        last_updated_at="2026-07-28T00:00:00Z",
        ttl_ms=60_000,
        poll_interval_ms=100,
    )

    assert created.model_dump(by_alias=True, exclude_none=True) == {
        "resultType": "task",
        "taskId": "task-1",
        "status": "working",
        "createdAt": "2026-07-28T00:00:00Z",
        "lastUpdatedAt": "2026-07-28T00:00:00Z",
        "ttlMs": 60_000,
        "pollIntervalMs": 100,
    }
    assert GetTaskRequest.name_param == "taskId"
    assert CancelTaskRequest.name_param == "taskId"
    assert UpdateTaskRequest.name_param == "taskId"

    # mcp-types 2.0 intentionally retains the incompatible, nested 2025-11-25
    # core Task types as types-only definitions. SEP-2663 is flat and uses
    # ttlMs/pollIntervalMs, so those legacy result models must not be reused.
    assert "task" in mcp_types.CreateTaskResult.model_fields
    assert "task_id" not in mcp_types.CreateTaskResult.model_fields
    assert "task_id" in CreateTaskResult.model_fields


@pytest.mark.asyncio()
async def test_streamable_http_task_poll_sends_mcp_name_header():
    task_names: list[str] = []
    server = FastMCP("task-http-routing")
    server.add_extension(NomadTasksExtension(tool_names={"add"}))

    @server.tool(task=True)
    async def add(left: int, right: int) -> int:
        return left + right

    app = server.http_app(
        path="/mcp",
        middleware=[Middleware(CaptureMcpNameHeader, values=task_names)],
        stateless_http=True,
    )
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    http_server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", lifespan="on")
    )
    server_task = asyncio.create_task(http_server.serve(sockets=[listener]))

    async def wait_until_started() -> None:
        while not http_server.started:
            if server_task.done():
                await server_task
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(wait_until_started(), timeout=5)
        async with Client(
            f"http://127.0.0.1:{port}/mcp",
            extensions=[NomadTasksClientExtension()],
        ) as client:
            created = await client.session.call_tool(
                "add",
                {"left": 20, "right": 22},
                allow_claimed=True,
            )
            assert isinstance(created, CreateTaskResult)
            result = await _wait_for_terminal(client, created.task_id)
            assert result.result is not None
            assert result.result["structuredContent"] == {"result": 42}

        assert created.task_id in task_names
    finally:
        http_server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)
        listener.close()
