from __future__ import annotations

import logging
from typing import Any

from mcp import ClientSession
from mcp.client.session_group import ClientSessionGroup
from mcp.types import CallToolResult, Tool

from ..common.upstream_errors import UpstreamConnectionError

logger = logging.getLogger(__name__)


class UpstreamProxy:
    """Async helper that maintains connections to upstream MCP servers."""

    def __init__(self, servers: dict[str, Any]):
        self._servers = dict(servers)
        self._group: ClientSessionGroup | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._tool_name_map: dict[str, dict[str, str]] = {}
        self._component_alias: str | None = None

    @property
    def servers(self) -> list[str]:
        return list(self._servers.keys())

    async def start(self) -> None:
        if self._group is not None:
            return
        group = ClientSessionGroup(component_name_hook=self._component_name)
        await group.__aenter__()
        self._group = group
        for server, params in self._servers.items():
            await self._connect_server(server, params)

    async def stop(self) -> None:
        if self._group is None:
            return
        try:
            await self._group.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to close upstream session group")
        finally:
            self._group = None
            self._sessions.clear()
            self._tool_name_map.clear()

    async def ensure_ready(self) -> None:
        if self._group is None:
            await self.start()

    def _component_name(self, name: str, server_info: Any) -> str:
        alias = self._component_alias or getattr(server_info, "name", None)
        if not alias:
            alias = "server"
        return f"{alias}:{name}"

    def _require_group(self) -> ClientSessionGroup:
        if self._group is None:
            raise RuntimeError("UpstreamProxy has not been started")
        return self._group

    async def _connect_server(self, server: str, params: Any) -> None:
        group = self._require_group()
        if server in self._sessions:
            return
        if server not in self._servers:
            raise KeyError(f"Unknown upstream server '{server}'")

        before = set(group.tools.keys())
        self._component_alias = server
        try:
            session = await group.connect_to_server(params)
        except Exception as exc:
            raise UpstreamConnectionError(server, params, exc) from exc
        finally:
            self._component_alias = None

        after = set(group.tools.keys())
        new_keys = after - before
        if not new_keys:
            prefix = f"{server}:"
            new_keys = {name for name in after if name.startswith(prefix)}

        mapping: dict[str, str] = {}
        for prefixed in new_keys:
            original = prefixed.split(":", 1)[1] if ":" in prefixed else prefixed
            mapping[original] = prefixed
        self._sessions[server] = session
        self._tool_name_map[server] = mapping

    async def list_tools(self, server: str) -> list[Tool]:
        await self.ensure_ready()
        group = self._require_group()
        if server not in self._sessions:
            await self._connect_server(server, self._servers[server])
        mapping = self._tool_name_map.get(server)
        if not mapping:
            prefix = f"{server}:"
            mapping = {
                name.split(":", 1)[1] if ":" in name else name: name
                for name in group.tools.keys()
                if name.startswith(prefix)
            }
            self._tool_name_map[server] = mapping
        tools: list[Tool] = []
        for original, prefixed in mapping.items():
            tool = group.tools.get(prefixed)
            if tool is None:
                continue
            tools.append(tool.model_copy(update={"name": original}))
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
        await self.ensure_ready()
        group = self._require_group()
        if server not in self._sessions:
            await self._connect_server(server, self._servers[server])
        mapping = self._tool_name_map.get(server)
        if mapping is None or tool_name not in mapping:
            await self.list_tools(server)
            mapping = self._tool_name_map.get(server, {})
        prefixed = mapping.get(tool_name)
        if prefixed is None or prefixed not in group.tools:
            raise ValueError(f"Tool '{tool_name}' not found on server '{server}'")
        return await group.call_tool(prefixed, arguments)
