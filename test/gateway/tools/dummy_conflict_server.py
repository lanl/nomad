#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fastmcp",
# ]
# ///
from fastmcp import FastMCP

mcp = FastMCP()


@mcp.tool(name="foo-bar")
def foo_bar_dash() -> str:
    """Tool whose published name sanitizes to foo_bar."""
    return "dash"


@mcp.tool(name="foo_bar")
def foo_bar_underscore() -> str:
    """Tool whose published name also sanitizes to foo_bar."""
    return "underscore"


if __name__ == "__main__":
    mcp.run(show_banner=False)
