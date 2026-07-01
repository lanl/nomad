#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fastmcp",
# ]
# ///
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import torch
from fastmcp import FastMCP
from pydantic import BaseModel, Field, PrivateAttr

from nomad.config import ToolManagerConfig
from nomad.fm_base_tool import TorchModuleTool
from nomad.torch_tool_manager import TorchModelToolManager


class SciFMArguments(BaseModel):
    value: float = Field(description="Input value to scale")


class SciFMResult(BaseModel):
    tool: str
    value: float
    batch_size: int
    device: str


class _DummyModule(torch.nn.Module):
    def __init__(self, multiplier: float) -> None:
        super().__init__()
        self.multiplier = multiplier

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:  # type: ignore[override]
        return inputs


class DummySciFMTool(
    TorchModuleTool[
        SciFMArguments,
        SciFMResult,
        torch.Tensor,
        dict[str, Any],
    ]
):
    """Lightweight stand-in for a SciFM TorchModuleTool."""

    args_schema: type[SciFMArguments] = SciFMArguments
    output_schema: type[SciFMResult] = SciFMResult

    _current_batch: list[SciFMArguments] = PrivateAttr(default_factory=list)
    _multiplier: float = PrivateAttr(default=1.0)

    def __init__(
        self,
        name: str,
        *,
        multiplier: float,
        batch_size: int = 4,
    ) -> None:
        fm = _DummyModule(multiplier)
        super().__init__(
            fm=fm,
            name=name,
            description=f"Dummy SciFM {name}",
            batch_size=batch_size,
            device=torch.device("cpu"),
        )
        self._multiplier = multiplier

    def preprocess(self, inputs: Sequence[SciFMArguments]) -> torch.Tensor:
        self._current_batch = list(inputs)
        tensor = torch.tensor(
            [float(item.value) for item in self._current_batch],
            dtype=torch.float32,
            device=self.device,
        )
        return tensor

    def _forward(self, model_inputs: torch.Tensor) -> dict[str, Any]:
        outputs = model_inputs * self._multiplier
        batch_size = int(outputs.shape[0]) if outputs.ndim > 0 else 1
        batch_size = max(batch_size, self.batch_size)
        return {
            "values": outputs.detach().cpu().tolist(),
            "batch_size": batch_size,
            "device": str(self.device),
        }

    def postprocess(self, model_output: dict[str, Any]):
        values = model_output["values"]
        batch_size = model_output["batch_size"]
        device = model_output["device"]
        for value, original in zip(values, self._current_batch):
            yield SciFMResult(
                tool=self.name,
                value=float(value),
                batch_size=batch_size,
                device=device,
            )
        self._current_batch.clear()


class BatchingTorchModelToolManager(TorchModelToolManager):
    def __init__(self, *args, batch_delay: float = 0.05, **kwargs):
        super().__init__(*args, **kwargs)
        self._batch_delay = batch_delay

    async def _execute_request(self, *args, **kwargs) -> None:
        if self._batch_delay:
            await asyncio.sleep(self._batch_delay)
        await super()._execute_request(*args, **kwargs)


def main() -> None:
    server = FastMCP("Dummy SciFM Gateway")
    manager = BatchingTorchModelToolManager(
        ToolManagerConfig(idle_seconds=0.0),
        device_provider=lambda: [torch.device("cpu")],
        batch_delay=0.05,
    )
    tools = [
        DummySciFMTool("scifm_alpha", multiplier=1.5, batch_size=4),
        DummySciFMTool("scifm_beta", multiplier=2.0, batch_size=4),
    ]
    for tool in tools:
        manager.register_tool(tool.name, tool, source=f"dummy://{tool.name}")
    manager.add_to_fastmcp(server)
    server.run()


if __name__ == "__main__":
    main()
