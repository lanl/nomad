#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fastmcp",
#     "pydantic",
# ]
# ///
import torch
from fastmcp import FastMCP
from pydantic import BaseModel

from nomad.well_format import BoundaryCondition, WellFormat

mcp = FastMCP()


class EchoPayload(BaseModel):
    color: str
    day: str
    delta: str


@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b


@mcp.tool
def multiply(a: int, b: int) -> int:
    """Multiply two numbers"""
    return a * b


@mcp.tool
def greet(name: str, excited: bool | None = None) -> str:
    """Return a friendly greeting."""
    suffix = "!" if excited else "."
    return f"Hello, {name}{suffix}"


@mcp.tool
def echo_object(a: dict[str, int]) -> dict[str, int]:
    """Return an object argument unchanged."""
    return a


@mcp.tool
def echo_payload(a: EchoPayload) -> dict[str, str]:
    """Return a validated payload unchanged."""
    return a.model_dump()


@mcp.tool
def make_well_format() -> WellFormat:
    """Return a small WellFormat payload with tensor fields."""
    return WellFormat(
        dataset_name="dummy",
        grid_type="cartesian",
        n_spatial_dims=1,
        boundary_conditions={
            "wall": BoundaryCondition(
                bc_type="dirichlet",
                mask=torch.tensor([True, False]),
                values=torch.tensor([1.0]),
            )
        },
        t0_fields={"temperature": torch.tensor([1.0, 2.0])},
    )


@mcp.tool
def boom() -> str:
    """Raise an error for testing."""
    raise RuntimeError("boom from tool")


if __name__ == "__main__":
    mcp.run(show_banner=False)
