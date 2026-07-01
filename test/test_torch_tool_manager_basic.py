from __future__ import annotations

import pytest
import torch
from pydantic import BaseModel

from nomad.fm_base_tool import TorchModuleTool
from nomad.torch_tool_manager import TorchModelToolManager


class DummyInput(BaseModel):
    value: int


class DummyOutput(BaseModel):
    value: int


class DummyTool(TorchModuleTool[DummyInput, DummyOutput, torch.Tensor, torch.Tensor]):
    args_schema: type[DummyInput] = DummyInput
    output_schema: type[DummyOutput] = DummyOutput
    description: str = "double"

    def preprocess(self, inputs):
        values = [item.value for item in inputs]
        return torch.tensor(values, dtype=torch.float32)

    def _forward(self, tensor):
        return tensor * 2

    def postprocess(self, outputs):
        for value in outputs.tolist():
            yield DummyOutput(value=int(value))


@pytest.mark.asyncio()
async def test_tool_manager_dispatch_round_trip():
    manager = TorchModelToolManager(device_provider=lambda: [torch.device("cpu")])
    tool = DummyTool(
        fm=torch.nn.Identity(),
        batch_size=1,
        device=torch.device("cpu"),
        name="doubler",
        description="double",
    )
    manager.register_tool("doubler", tool, source="dummy://doubler")

    result1 = await manager.call_tool("doubler", {"value": 3})
    assert result1.value == 6

    result2 = await manager.call_tool("doubler", {"value": 4})
    assert result2.value == 8

    await manager.aclose()
