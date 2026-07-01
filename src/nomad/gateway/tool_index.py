from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from mcp.types import Tool

from nomad.config import SearchToolConfig
from nomad.tool_search import (
    SearchDetail,
    ToolDescriptor,
    ToolSearchEngine,
    build_tool_descriptors,
    descriptor_payload,
)

from .runtime.constants import DEFAULT_WRAPPERS_PACKAGE
from .upstream import UpstreamProxy


class ToolIndex:
    """Cache indexed tool metadata from upstream servers."""

    def __init__(
        self,
        upstream: UpstreamProxy,
        *,
        search_config: SearchToolConfig | None = None,
        wrappers_package: str = DEFAULT_WRAPPERS_PACKAGE,
    ):
        self._upstream = upstream
        self._search_config = search_config or SearchToolConfig()
        self._wrappers_package = wrappers_package
        self._tool_cache: dict[str, tuple[ToolDescriptor, ...]] = {}
        self._search_cache: dict[str, ToolSearchEngine] = {}
        self._combined_search_cache: tuple[tuple[str, ...], ToolSearchEngine] | None = (
            None
        )
        self._lock = asyncio.Lock()

    def _build_descriptors(
        self, server: str, tools: Iterable[Tool]
    ) -> tuple[ToolDescriptor, ...]:
        return build_tool_descriptors(
            server,
            tools,
            wrappers_package=self._wrappers_package,
        )

    def _build_search_engine(
        self,
        descriptors: Iterable[ToolDescriptor],
    ) -> ToolSearchEngine:
        return ToolSearchEngine(tuple(descriptors), config=self._search_config)

    async def _refresh_server(self, server: str) -> tuple[ToolDescriptor, ...]:
        async with self._lock:
            tools = await self._upstream.list_tools(server)
            descriptors = self._build_descriptors(server, tools)
            self._tool_cache[server] = descriptors
            self._search_cache[server] = self._build_search_engine(descriptors)
            self._combined_search_cache = None
            return descriptors

    async def _get_server_tools(self, server: str) -> tuple[ToolDescriptor, ...]:
        tools = self._tool_cache.get(server)
        if tools is not None:
            return tools
        return await self._refresh_server(server)

    async def _get_server_search_engine(self, server: str) -> ToolSearchEngine:
        engine = self._search_cache.get(server)
        if engine is not None:
            return engine

        await self._refresh_server(server)
        return self._search_cache[server]

    async def _get_combined_search_engine(
        self,
        servers: tuple[str, ...],
    ) -> ToolSearchEngine:
        cached = self._combined_search_cache
        if cached is not None and cached[0] == servers:
            return cached[1]

        descriptors: list[ToolDescriptor] = []
        for server in servers:
            descriptors.extend(await self._get_server_tools(server))

        engine = self._build_search_engine(descriptors)
        self._combined_search_cache = (servers, engine)
        return engine

    async def list_server_tools(self, server: str) -> list[ToolDescriptor]:
        return list(await self._get_server_tools(server))

    async def search(
        self,
        query: str | None,
        server_filter: str | None = None,
        limit: int | None = None,
    ) -> list[ToolDescriptor]:
        servers = tuple(
            [server_filter] if server_filter is not None else self._upstream.servers
        )
        if not servers:
            return []

        if len(servers) == 1:
            engine = await self._get_server_search_engine(servers[0])
        else:
            engine = await self._get_combined_search_engine(servers)

        return list(engine.search(query, limit=limit))

    async def search_code_payload(
        self,
        query: str | None,
        server_filter: str | None = None,
        detail_level: SearchDetail = "brief",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        results = await self.search(
            query=query,
            server_filter=server_filter,
            limit=limit,
        )
        return [
            descriptor_payload(
                descriptor,
                detail_level=detail_level,
                surface="code-mode",
            )
            for descriptor in results
        ]

    async def get_schema(self, server: str, tool_name: str) -> dict[str, Any]:
        tools = await self._get_server_tools(server)
        tool = next((tool for tool in tools if tool.name == tool_name), None)
        if tool is None:
            tools = await self._refresh_server(server)
            tool = next((item for item in tools if item.name == tool_name), None)
        if tool is None:
            raise ValueError(f"Tool '{tool_name}' not found on server '{server}'")
        return tool.schema_copy()

    async def list_server_schemas(self, server: str) -> list[dict[str, Any]]:
        tools = await self._get_server_tools(server)
        return [tool.schema_copy() for tool in tools]
