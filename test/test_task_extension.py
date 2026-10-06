from __future__ import annotations

import asyncio
import time
import types
from datetime import timedelta

import pytest
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.utilities.tasks import TaskConfig
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import HEADER_MISMATCH, MISSING_REQUIRED_CLIENT_CAPABILITY

from nomad import task_extension as task_module
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
    _task_owner,
    _TaskRecord,
)


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
                    input_responses={"unused": {}},
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
    assert result.error == {
        "code": MISSING_REQUIRED_CLIENT_CAPABILITY,
        "message": "missing required capability",
    }


@pytest.mark.asyncio()
async def test_task_capacity_is_bounded():
    server = FastMCP("task-capacity")
    server.add_extension(NomadTasksExtension(tool_names={"blocked"}, max_records=1))
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

        with pytest.raises(MCPError, match="too many retained tasks"):
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
        with pytest.raises(TimeoutError):
            await client.call_tool("slow", {}, read_timeout_seconds=0.05)

        assert time.monotonic() - started < 0.15


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
    runners = tuple(extension._runners)
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


def test_task_owner_uses_canonical_subject_and_prevents_cross_user_lookup(
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
    monkeypatch.setattr(task_module, "get_access_token", lambda: current_token)
    owner = _task_owner()
    extension = NomadTasksExtension(tool_names={"model"})
    now = "2026-10-05T00:00:00+00:00"
    extension._records["secret"] = _TaskRecord(
        task_id="secret",
        owner=owner,
        created_at=now,
        updated_at=now,
        created_monotonic=time.monotonic(),
        ttl_ms=1000,
        poll_interval_ms=20,
    )

    current_token = current_token.model_copy(
        update={"token": "bob-token", "subject": "bob"}
    )

    with pytest.raises(MCPError, match="not found"):
        extension._lookup("secret")


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
