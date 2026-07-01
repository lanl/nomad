from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from fastmcp import Client
from mcp.client.stdio import StdioServerParameters

from nomad.gateway.config import GatewayConfig, GatewayDefaults
from nomad.gateway.server import CodeModeGateway

DUMMY_SERVER = Path(__file__).parent / "tools" / "dummy_mcp_server.py"


@dataclass(slots=True)
class GatewayHarness:
    client: Client
    gateway: CodeModeGateway


def _is_subpath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


async def _wait_for_path_absent(path: Path, timeout: float = 2.0) -> None:
    deadline = time.perf_counter() + timeout
    while path.exists() and time.perf_counter() < deadline:
        await asyncio.sleep(0.05)
    assert not path.exists(), f"Path '{path}' still exists after cleanup interval"


@pytest_asyncio.fixture
async def ephemeral_gateway(tmp_path: Path):
    servers = {
        "dummy": StdioServerParameters(
            command=sys.executable,
            args=[str(DUMMY_SERVER.resolve())],
            env=os.environ.copy(),
        )
    }
    defaults = GatewayDefaults(
        timeout_seconds=10,
        stdout_limit=64,
        stderr_limit=64,
        result_limit=256,
        workspace_root=None,
    )
    config = GatewayConfig(servers=servers, defaults=defaults, middleware=[])
    async with CodeModeGateway(config) as gateway_obj:
        async with Client(gateway_obj.fastmcp) as client:
            await client.initialize()
            yield GatewayHarness(client=client, gateway=gateway_obj)


@pytest.mark.asyncio
async def test_ephemeral_workspace_cleanup(ephemeral_gateway: GatewayHarness):
    sandbox = ephemeral_gateway.gateway._sandbox

    payload = await ephemeral_gateway.client.call_tool(
        "execute_mcp_code", {"code": "import os\nRESULT = os.getcwd()"}
    )
    first_workspace = Path(payload.data["result"])
    assert first_workspace.name.startswith("nomad_gateway_workspace_")

    wrappers_root = sandbox.wrappers_root
    assert wrappers_root.exists()
    assert not _is_subpath(wrappers_root, first_workspace)

    if first_workspace.exists():
        await _wait_for_path_absent(first_workspace)

    payload = await ephemeral_gateway.client.call_tool(
        "execute_mcp_code", {"code": "import os\nRESULT = os.getcwd()"}
    )
    second_workspace = Path(payload.data["result"])
    assert second_workspace != first_workspace
    assert second_workspace.name.startswith("nomad_gateway_workspace_")

    if second_workspace.exists():
        await _wait_for_path_absent(second_workspace)


@pytest.mark.asyncio
async def test_ephemeral_wrapper_cleanup(tmp_path: Path):
    servers = {
        "dummy": StdioServerParameters(
            command=sys.executable,
            args=[str(DUMMY_SERVER.resolve())],
            env=os.environ.copy(),
        )
    }
    defaults = GatewayDefaults(
        timeout_seconds=10,
        stdout_limit=64,
        stderr_limit=64,
        result_limit=256,
        workspace_root=None,
    )
    config = GatewayConfig(servers=servers, defaults=defaults, middleware=[])

    async with CodeModeGateway(config) as gateway_obj:
        wrappers_root = gateway_obj._sandbox.wrappers_root
        assert wrappers_root.exists()

    await _wait_for_path_absent(wrappers_root)
