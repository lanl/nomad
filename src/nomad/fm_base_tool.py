from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import islice
from typing import final

import torch
from fastmcp import FastMCP
from fastmcp.tools import FunctionTool as FastMCPTool
from pydantic import BaseModel, ConfigDict, Field
from torch.accelerator import current_accelerator
from torch.utils.data import default_collate

from ._torch_module_compat import (
    add_torch_module_tool_to_fastmcp,
    build_torch_module_fastmcp_tool,
)


def batched(iterable, n):
    """Yield tuples of up to ``n`` items from ``iterable``."""
    if n < 1:
        raise ValueError("n must be at least one")
    it = iter(iterable)
    while batch := tuple(islice(it, n)):
        yield batch


def default_device() -> torch.device:
    """Return the current accelerator device, falling back to CPU."""
    return current_accelerator(check_available=True) or torch.device("cpu")


class TorchModuleTool[Input: BaseModel, Output: BaseModel, ModelInput, ModelOutput](
    BaseModel
):
    """
    A helper class for exposing a PyTorch model as an MCP tool for inference.
    Provides default methods for running the following pipeline:

    1. Preprocess a sequence of `Inputs` into a `ModelInput`
    2. Pass `ModelInput` through the PyTorch model getting `ModelOutput`
    3. Postprocess `ModelOutput` into a suitable sequence of `Outputs`

    Complex models (i.e. multi-GPU) may not be fully supported by this class.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    fm: torch.nn.Module
    """The underlying PyTorch model used for inference."""

    name: str | None = None
    """A short name for the foundation model."""

    description: str
    """What the foundation model does / how to use it."""

    args_schema: type[Input]
    """The input schema for the model."""

    output_schema: type[Output]
    """The output schema for the model."""

    batch_size: int = 1
    """Inputs to the model will be batched into sets of at most this size."""

    device: torch.device = Field(default_factory=default_device)
    """The accelerator on which the model is placed."""

    def preprocess(self, input: Sequence[Input]) -> ModelInput:
        """
        Convert tool input into the form accepted by the model.
        The input will be of type `list[args_schema]` with a length
        of `batch_size`.

        Defaults to `torch.data.default_collate`.
        """
        return default_collate(list(input))

    def _forward(self, model_inputs: ModelInput) -> ModelOutput:
        """Process a batch of observations with the model."""
        return self.fm(model_inputs).to("cpu")

    def postprocess(self, model_output: ModelOutput) -> Iterable[Output]:
        """Postprocess the model's raw output into a relevant tool output format."""
        yield from model_output

    @classmethod
    def from_pretrained(
        cls, pretrained_model_name_or_path: str, **kwargs
    ) -> TorchModuleTool:
        """Instantiate tool from a pretrained checkpoint on disk or a remote repo."""
        raise NotImplementedError()

    def model_post_init(self, __context) -> None:
        self.fm = self.fm.to(self.device)
        if self.name is None:
            self.name = self.__class__.__name__

    @final
    def batch(self, inputs: list[Input], **kwargs) -> list[Output]:
        return list(self.batch_as_completed(inputs, **kwargs))

    @final
    def batch_as_completed(
        self,
        inputs: list[Input],
        max_concurency: int | None = None,
    ) -> Iterable[Output]:
        n = max_concurency or self.batch_size
        for batch in batched(inputs, n=n):
            with torch.inference_mode():
                batch = self.preprocess(batch)
                y = self._forward(batch)
                yield from self.postprocess(y)

    @final
    def __call__(self, input: Input):
        with torch.inference_mode():
            batch = self.preprocess([input])
            y = self._forward(batch)
            return next(iter(self.postprocess(y)))

    @final
    def __to_fastmcp(self) -> FastMCPTool:
        return build_torch_module_fastmcp_tool(self)

    @final
    def add_to_fastmcp(self, server: FastMCP) -> FastMCPTool:
        """Add `self` as a tool to `server`."""
        return add_torch_module_tool_to_fastmcp(server, self)


__all__ = ["TorchModuleTool", "default_device"]
