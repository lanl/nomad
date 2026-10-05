from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import torch
from fastmcp import FastMCP

from nomad.config import ToolManagerConfig
from nomad.model_env import ModelEnvironment
from nomad.model_rpc import ModelProcess, RemoteModel, inspect_remote_model
from nomad.torch_tool_manager import TorchModelToolManager


@dataclass(frozen=True)
class ExistingEnvironment:
    python: Path
    checksum: str = "existing"

    def ensure(self) -> Path:
        return self.python


def test_model_process_json_rpc_round_trip(monkeypatch, tmp_path: Path):
    module = tmp_path / "isolated_fixture.py"
    module.write_text(
        """
from pydantic import BaseModel

class Input(BaseModel):
    value: int

class Output(BaseModel):
    value: int

class Module:
    def to(self, device):
        self.device = device
        return self

class Tool:
    name = "isolated"
    description = "isolated fixture"
    args_schema = Input
    output_schema = Output
    batch_size = 2

    def __init__(self):
        self.fm = Module()

    @classmethod
    def from_pretrained(cls, source):
        print("model stdout is not protocol output")
        return cls()

    def batch_as_completed(self, inputs, max_concurency=None):
        for item in inputs:
            yield Output(value=item.value + 1)
""",
        encoding="utf-8",
    )
    current_pythonpath = str(tmp_path)
    if inherited := os.environ.get("PYTHONPATH"):
        current_pythonpath += os.pathsep + inherited
    monkeypatch.setenv("PYTHONPATH", current_pythonpath)
    environment = ExistingEnvironment(Path(sys.executable))

    remote = inspect_remote_model(
        environment=environment,  # type: ignore[arg-type]
        model_class="isolated_fixture.Tool",
        source="weights",
        tool_name=None,
        batch_size=None,
    )

    assert remote.name == "isolated"
    assert remote.input_schema["properties"]["value"]["type"] == "integer"
    with ModelProcess(environment) as process:  # type: ignore[arg-type]
        process.load(remote, "cpu")
        assert process.run_batch([{"value": 2}], batch_size=1) == [{"value": 3}]


@pytest.mark.asyncio
async def test_slot_reuses_worker_for_same_environment_and_replaces_for_new_one(
    monkeypatch,
):
    created = []

    class FakeProcess:
        def __init__(self, environment):
            self.environment = environment
            self.alive = True
            self.closed = False
            self.pid = len(created) + 1
            created.append(self)

        def load(self, remote, device):
            self.remote = remote
            return {}

        def run_batch(self, inputs, *, batch_size):
            return [{"value": item["value"] + 1} for item in inputs]

        def request(self, method, params=None):
            return None

        def close(self):
            self.closed = True
            self.alive = False

    monkeypatch.setattr("nomad.torch_tool_manager.ModelProcess", FakeProcess)
    first_env = ModelEnvironment("requirements.txt", b"same")
    second_env = ModelEnvironment("requirements.txt", b"different")

    def remote(name: str, environment: ModelEnvironment) -> RemoteModel:
        schema = {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        }
        return RemoteModel(
            environment=environment,
            model_class="fixture.Tool",
            source=name,
            name=name,
            description=name,
            batch_size=1,
            input_schema=schema,
            output_schema=schema,
        )

    manager = TorchModelToolManager(
        ToolManagerConfig(
            idle_seconds=None,
            disk_idle_seconds=None,
            gc_idle_seconds=None,
            venv_idle_seconds=30,
        ),
        device_provider=lambda: [torch.device("cpu")],
    )
    manager.register_remote_tool(remote("first", first_env))
    manager.register_remote_tool(remote("second", first_env))
    manager.register_remote_tool(remote("third", second_env))

    fast_tools = manager.add_to_fastmcp(FastMCP())
    assert await fast_tools["first"].fn(value=1) == {"value": 2}
    first_process = created[0]
    assert await manager.call_tool("second", {"value": 2}) == {"value": 3}
    assert created == [first_process]
    assert not first_process.closed

    assert await manager.call_tool("third", {"value": 3}) == {"value": 4}
    assert first_process.closed
    assert len(created) == 2

    slot = manager._device_slots[0]
    await manager._evict_idle_workers(now=slot.last_used + 31)
    assert created[-1].closed
    assert slot.worker is None
    await manager.aclose()
