from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult, Tool

from ..common.upstream_errors import UpstreamConnectionError
from .config import ServerParameters

if TYPE_CHECKING:
    from fastmcp import Client

logger = logging.getLogger(__name__)


class UpstreamProxy:
    """Async helper that maintains connections to upstream MCP servers."""

    def __init__(self, servers: dict[str, ServerParameters]):
        self._servers = dict(servers)
        self._clients: dict[str, Client] = {}
        self._tool_names: dict[str, set[str]] = {}

    @property
    def servers(self) -> list[str]:
        return list(self._servers.keys())

    async def start(self) -> None:
        for server, params in self._servers.items():
            await self._connect_server(server, params)

    async def stop(self) -> None:
        errors: list[Exception] = []
        for server, client in reversed(self._clients.items()):
            try:
                await client.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to close upstream MCP client '%s'", server)
                errors.append(exc)
        self._clients.clear()
        self._tool_names.clear()
        if errors:
            raise errors[-1]

    async def _connect_server(self, server: str, params: ServerParameters) -> None:
        if server in self._clients:
            return
        if server not in self._servers:
            raise KeyError(f"Unknown upstream server '{server}'")

        from fastmcp import Client

        client = Client(params.to_transport(), name=server)
        try:
            await client.__aenter__()
        except Exception as exc:
            raise UpstreamConnectionError(server, params, exc) from exc

        self._clients[server] = client

    async def _get_client(self, server: str) -> Client:
        if server not in self._servers:
            raise KeyError(f"Unknown upstream server '{server}'")
        if server not in self._clients:
            await self._connect_server(server, self._servers[server])
        return self._clients[server]

    async def _refresh_tool_names(self, server: str, client: Client) -> set[str]:
        tool_names = {tool.name for tool in await client.list_tools()}
        self._tool_names[server] = tool_names
        return tool_names

    async def list_tools(self, server: str) -> list[Tool]:
        client = await self._get_client(server)
        tools = await client.list_tools()
        self._tool_names[server] = {tool.name for tool in tools}
        return tools

    async def get_tool(self, server: str, tool_name: str) -> Tool | None:
        tools = await self.list_tools(server)
        for tool in tools:
            if tool.name == tool_name:
                return tool
        return None

    async def call_tool(
        self,
        server: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        client = await self._get_client(server)
        tool_names = self._tool_names.get(server)
        if tool_names is None:
            tool_names = await self._refresh_tool_names(server, client)
        if tool_name not in tool_names:
            tool_names = await self._refresh_tool_names(server, client)
        if tool_name not in tool_names:
            raise ValueError(f"Tool '{tool_name}' not found on server '{server}'")
        return await client.call_tool_mcp(tool_name, arguments)
