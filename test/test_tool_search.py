from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP
from mcp.types import Tool

from nomad import tool_search
from nomad.config import SearchToolConfig
from nomad.gateway.tool_index import ToolIndex
from nomad.tool_search import ToolDescriptor, search_tool_descriptors


def _descriptor(
    name: str, *, description: str | None = None, arguments=()
) -> ToolDescriptor:
    return ToolDescriptor(
        server="dummy",
        name=name,
        identifier=name.replace("-", "_"),
        title=None,
        description=description,
        signature="() -> None",
        arguments=tuple(arguments),
        schema={},
    )


def test_search_ranks_name_matches_exact_then_prefix_then_suffix_then_substring():
    descriptors = [
        _descriptor("alphabetabeta"),
        _descriptor("betamax"),
        _descriptor("omega_beta"),
        _descriptor("beta"),
    ]

    results = search_tool_descriptors(descriptors, query="beta")

    assert [item.name for item in results] == [
        "beta",
        "betamax",
        "omega_beta",
        "alphabetabeta",
    ]


def test_search_matches_arguments_and_description():
    descriptors = [
        _descriptor("adder", description="Add two numbers"),
        _descriptor(
            "greeter", description="Friendly tool", arguments=("name", "excited")
        ),
    ]

    argument_results = search_tool_descriptors(descriptors, query="excited")
    description_results = search_tool_descriptors(descriptors, query="numbers")

    assert [item.name for item in argument_results] == ["greeter"]
    assert [item.name for item in description_results] == ["adder"]


def test_descriptor_python_import_path_uses_wrapper_package():
    descriptor = ToolDescriptor(
        server="dummy",
        name="add",
        identifier="add",
        title=None,
        description=None,
        signature="() -> None",
        wrappers_package="custom_tools",
        schema={},
    )

    assert descriptor.python_import_path == "custom_tools.dummy.add"


def test_search_limit_none_returns_all_matches():
    descriptors = [
        _descriptor("alpha"),
        _descriptor("alphabet"),
        _descriptor("beta_alpha_gamma"),
    ]

    results = search_tool_descriptors(descriptors, query="alpha", limit=None)

    assert [item.name for item in results] == [
        "alpha",
        "alphabet",
        "beta_alpha_gamma",
    ]


def test_search_honors_custom_name_weights():
    descriptors = [
        _descriptor("foo_suffix"),
        _descriptor("prefix_foo"),
    ]
    config = SearchToolConfig(
        weights={
            "prefix_name": 9.0,
            "suffix_name": 1.0,
        }
    )

    results = search_tool_descriptors(
        descriptors,
        query="foo",
        config=config,
    )

    assert [item.name for item in results] == ["foo_suffix", "prefix_foo"]


def test_search_rejects_negative_limit():
    descriptors = [_descriptor("alpha")]

    with pytest.raises(ValueError, match="limit must be >= 0 or None"):
        search_tool_descriptors(descriptors, query="alpha", limit=-1)


def test_register_search_tool_reuses_cached_engine(monkeypatch):
    server = FastMCP("test")

    def add_numbers(a: int, b: int) -> int:
        return a + b

    server.add_tool(add_numbers)

    build_count = 0
    real_build_index = tool_search.ToolSearchEngine._build_index

    def counted_build_index(descriptors):
        nonlocal build_count
        build_count += 1
        return real_build_index(descriptors)

    monkeypatch.setattr(
        tool_search.ToolSearchEngine,
        "_build_index",
        staticmethod(counted_build_index),
    )

    tool_search.register_search_tool(
        server,
        search_config=SearchToolConfig(expose=True),
    )

    search_tool = asyncio.run(server.get_tool("search_tools"))
    assert search_tool is not None

    asyncio.run(search_tool.fn(query="add"))
    asyncio.run(search_tool.fn(query="numbers"))

    assert build_count == 1


@pytest.mark.asyncio
async def test_tool_index_reuses_cached_search_engine(monkeypatch):
    class DummyUpstream:
        servers = ["dummy"]

        async def list_tools(self, server: str) -> list[Tool]:
            assert server == "dummy"
            return [
                Tool(
                    name="add_numbers",
                    title=None,
                    description="Add two numbers",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "a": {"type": "integer"},
                            "b": {"type": "integer"},
                        },
                    },
                    outputSchema={"type": "integer"},
                )
            ]

    build_count = 0
    real_build_index = tool_search.ToolSearchEngine._build_index

    def counted_build_index(descriptors):
        nonlocal build_count
        build_count += 1
        return real_build_index(descriptors)

    monkeypatch.setattr(
        tool_search.ToolSearchEngine,
        "_build_index",
        staticmethod(counted_build_index),
    )

    index = ToolIndex(DummyUpstream(), search_config=SearchToolConfig())

    await index.search(query="add")
    await index.search(query="numbers")

    assert build_count == 1


@pytest.mark.asyncio
async def test_tool_index_code_payload_uses_configured_wrapper_package():
    class DummyUpstream:
        servers = ["dummy"]

        async def list_tools(self, server: str) -> list[Tool]:
            assert server == "dummy"
            return [
                Tool(
                    name="add_numbers",
                    title=None,
                    description="Add two numbers",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                    outputSchema={"type": "integer"},
                )
            ]

    index = ToolIndex(
        DummyUpstream(),
        search_config=SearchToolConfig(),
        wrappers_package="custom_tools",
    )

    payload = await index.search_code_payload(query="add")

    assert payload[0]["python_import_path"] == "custom_tools.dummy.add_numbers"
