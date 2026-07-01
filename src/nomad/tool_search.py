from __future__ import annotations

import copy
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import tantivy
from fastmcp import FastMCP

from .common.name_sanitize import sanitize_python_name
from .config import SearchToolConfig, SearchWeights
from .gateway.runtime.constants import DEFAULT_WRAPPERS_PACKAGE
from .gateway.runtime.wrapper_factory import build_tool_signature

SearchDetail = Literal["brief", "full"]
SearchSurface = Literal["mcp", "code-mode"]


def _build_tool_identifier_map(
    tool_names: Sequence[str],
    *,
    server: str,
) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    seen_by_identifier: dict[str, str] = {}
    for tool_name in tool_names:
        identifier = sanitize_python_name(tool_name)
        existing = seen_by_identifier.get(identifier)
        if existing is not None and existing != tool_name:
            raise ValueError(
                f"Tool name conflict on server '{server}': "
                f"{existing!r} and {tool_name!r} both sanitize to {identifier!r}"
            )
        seen_by_identifier[identifier] = tool_name
        identifiers[tool_name] = identifier
    return identifiers


def _extract_arguments(schema: dict[str, Any]) -> tuple[str, ...]:
    input_schema = schema.get("inputSchema")
    if not isinstance(input_schema, dict):
        return ()

    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return ()

    names: list[str] = []
    for name in properties:
        if isinstance(name, str):
            names.append(name)
    return tuple(names)


def _collapse_ws(value: str) -> str:
    return " ".join(value.split())


def _normalize_query(value: str | None) -> str:
    if value is None:
        return ""
    return _collapse_ws(value.strip().casefold())


def _normalize_name(value: str) -> str:
    return _collapse_ws(value.casefold())


def _term_query(value: str) -> str:
    terms = re.findall(r"[0-9A-Za-z_]+", value.casefold())
    if not terms:
        return ""
    return " ".join(terms)


def _name_match_boost(
    name: str, query: str, weights: SearchWeights
) -> tuple[int, float]:
    normalized_name = _normalize_name(name)
    if not query:
        return (0, 0.0)
    if normalized_name == query:
        return (4, weights.exact_name)
    if normalized_name.startswith(query):
        return (3, weights.prefix_name)
    if normalized_name.endswith(query):
        return (2, weights.suffix_name)
    if query in normalized_name:
        return (1, weights.substring_name)
    return (0, 0.0)


def _apply_limit[T](items: Sequence[T], limit: int | None) -> list[T]:
    if limit is None:
        return list(items)
    if limit < 0:
        raise ValueError("limit must be >= 0 or None")
    return list(items[:limit])


@dataclass(slots=True)
class ToolDescriptor:
    """Searchable metadata for one MCP tool."""

    server: str
    """Server ID that exposes the tool."""

    name: str
    """Original MCP tool name."""

    identifier: str
    """Python-safe identifier used by generated wrappers."""

    description: str | None
    """Tool description from the MCP schema."""

    title: str | None
    """Optional human-readable tool title."""

    signature: str
    """Python-callable signature rendered from the tool schema."""

    arguments: tuple[str, ...] = ()
    """Input argument names extracted from the input schema."""

    wrappers_package: str = DEFAULT_WRAPPERS_PACKAGE
    """Python package used as the root for generated wrappers."""

    schema: dict[str, Any] = field(default_factory=dict)
    """Full MCP tool schema."""

    @property
    def python_import_path(self) -> str:
        return f"{self.wrappers_package}.{self.server}.{self.identifier}"

    def schema_copy(self) -> dict[str, Any]:
        return copy.deepcopy(self.schema)

    def to_payload(
        self,
        *,
        detail_level: SearchDetail,
    ) -> dict[str, Any]:
        if detail_level == "full":
            return {
                "name": self.name,
                "description": self.description,
                "inputSchema": copy.deepcopy(self.schema.get("inputSchema")),
                "outputSchema": copy.deepcopy(self.schema.get("outputSchema")),
            }

        return {
            "name": self.name,
            "description": self.description,
        }

    def to_wrapper_entry(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "tool_name": self.name,
            "description": self.description,
            "schema": self.schema_copy(),
        }


def _tool_schema(tool: Any) -> dict[str, Any]:
    if hasattr(tool, "to_mcp_tool"):
        tool = tool.to_mcp_tool()
    return tool.model_dump(mode="json")


def build_tool_descriptors(
    server: str,
    tools: Iterable[Any],
    *,
    wrappers_package: str = DEFAULT_WRAPPERS_PACKAGE,
) -> tuple[ToolDescriptor, ...]:
    """Build searchable descriptors for tools exposed by one server."""
    tool_list = list(tools)
    identifier_map = _build_tool_identifier_map(
        [tool.name for tool in tool_list],
        server=server,
    )

    descriptors: list[ToolDescriptor] = []
    for tool in tool_list:
        schema = _tool_schema(tool)
        descriptors.append(
            ToolDescriptor(
                server=server,
                name=tool.name,
                identifier=identifier_map[tool.name],
                description=tool.description,
                title=tool.title,
                signature=str(build_tool_signature(schema)),
                arguments=_extract_arguments(schema),
                wrappers_package=wrappers_package,
                schema=schema,
            )
        )
    return tuple(descriptors)


class SupportsToolSearch(Protocol):
    """Protocol implemented by objects searchable with :class:`ToolSearchEngine`."""

    server: str
    name: str
    title: str | None
    description: str | None
    arguments: tuple[str, ...]
    python_import_path: str


class ToolSearchEngine:
    """Hybrid tool search using Tantivy retrieval plus deterministic reranking."""

    def __init__(
        self,
        descriptors: Sequence[SupportsToolSearch],
        *,
        config: SearchToolConfig | None = None,
    ):
        self._descriptors = list(descriptors)
        self._config = config or SearchToolConfig()
        self._index = self._build_index(self._descriptors)

    @staticmethod
    def _build_index(descriptors: Sequence[SupportsToolSearch]) -> tantivy.Index:
        builder = tantivy.SchemaBuilder()
        builder.add_unsigned_field("doc_id", stored=True)
        builder.add_text_field("name", stored=True)
        builder.add_text_field("python_import_path", stored=True)
        builder.add_text_field("title", stored=True)
        builder.add_text_field("description", stored=True)
        builder.add_text_field("arguments", stored=True)
        schema = builder.build()
        index = tantivy.Index(schema)
        writer = index.writer()
        for doc_id, descriptor in enumerate(descriptors):
            writer.add_document(
                tantivy.Document(
                    doc_id=doc_id,
                    name=[descriptor.name],
                    python_import_path=[descriptor.python_import_path],
                    title=[descriptor.title or ""],
                    description=[descriptor.description or ""],
                    arguments=list(descriptor.arguments),
                )
            )
        writer.commit()
        index.reload()
        return index

    def search(
        self,
        query: str | None,
        *,
        limit: int | None = None,
    ) -> list[SupportsToolSearch]:
        normalized_query = _normalize_query(query)
        if not normalized_query:
            return _apply_limit(self._descriptors, limit)

        weights = self._config.weights
        query_text = _term_query(normalized_query)
        tantivy_scores = self._tantivy_scores(query_text)

        ranked: list[tuple[int, float, str, str, SupportsToolSearch]] = []
        for index, descriptor in enumerate(self._descriptors):
            priority, name_boost = _name_match_boost(
                descriptor.name,
                normalized_query,
                weights,
            )
            tantivy_score = tantivy_scores.get(index, 0.0)
            combined_score = (tantivy_score * weights.tantivy_score) + name_boost
            if combined_score <= 0:
                continue
            ranked.append(
                (
                    priority,
                    combined_score,
                    descriptor.server,
                    descriptor.name,
                    descriptor,
                )
            )

        ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
        return _apply_limit([item[4] for item in ranked], limit)

    def _tantivy_scores(self, query_text: str) -> dict[int, float]:
        if not query_text:
            return {}

        weights = self._config.weights
        searcher = self._index.searcher()
        query, _errors = self._index.parse_query_lenient(
            query_text,
            ["name", "python_import_path", "title", "description", "arguments"],
            field_boosts={
                "name": weights.name_field,
                "python_import_path": weights.import_path_field,
                "title": weights.title_field,
                "description": weights.description_field,
                "arguments": weights.arguments_field,
            },
            conjunction_by_default=False,
        )
        candidate_limit = min(
            max(self._config.candidate_limit, 1),
            max(len(self._descriptors), 1),
        )
        results = searcher.search(query, limit=candidate_limit)
        scores: dict[int, float] = {}
        for score, address in results.hits:
            document = searcher.doc(address).to_dict()
            doc_values = document.get("doc_id")
            if not isinstance(doc_values, list) or not doc_values:
                continue
            doc_id = int(doc_values[0])
            scores[doc_id] = max(float(score), scores.get(doc_id, -math.inf))
        return scores


def search_tool_descriptors(
    descriptors: Sequence[SupportsToolSearch],
    *,
    query: str | None,
    limit: int | None = None,
    config: SearchToolConfig | None = None,
) -> list[SupportsToolSearch]:
    """Rank descriptors by query text and return matching items."""
    engine = ToolSearchEngine(descriptors, config=config)
    return engine.search(query, limit=limit)


def descriptor_payload(
    descriptor: ToolDescriptor,
    *,
    detail_level: SearchDetail,
    surface: SearchSurface,
) -> dict[str, Any]:
    """Render a descriptor payload for an MCP or code-mode search surface."""
    payload = descriptor.to_payload(detail_level=detail_level)
    if surface == "code-mode":
        payload.pop("name", None)
        payload["python_import_path"] = descriptor.python_import_path
        if detail_level == "brief":
            payload["signature"] = descriptor.signature
    return payload


def register_search_tool(
    server: FastMCP,
    *,
    search_config: SearchToolConfig,
    server_name: str = "nomad",
) -> None:
    """Register the built-in ``search_tools`` tool on a FastMCP server."""
    cached_engine: ToolSearchEngine | None = None

    @server.tool()
    async def search_tools(
        query: str | None,
        server_filter: str | None = None,
        detail_level: Literal["brief", "full"] = "brief",
        limit: int | None = 5,
    ) -> list[dict[str, Any]]:
        """Discover tools exposed by this Nomad server.

        :param query: Optional keyword search across tool names,
            descriptions, and argument metadata. Returns results
            for all tools, limited to ``limit``, if ``None``.
        :param server_filter: Optional server filter. Outside the gateway,
            only the local server is available.
        :param detail_level: ``"brief"`` (default) returns ``name`` and
            ``description``. ``"full"`` returns ``name``, ``description``,
            ``inputSchema``, and ``outputSchema``.
        :param limit: Maximum number of matching tools to return. Defaults to 5.
            Pass ``None`` to return all matches.
        :returns: A list of matching tools.
        """
        nonlocal cached_engine
        if server_filter not in (None, server_name):
            return []

        level: SearchDetail = "full" if detail_level == "full" else "brief"
        if cached_engine is None:
            tools = await server.list_tools()
            descriptors = build_tool_descriptors(
                server_name,
                [tool for tool in tools if tool.name != "search_tools"],
            )
            cached_engine = ToolSearchEngine(descriptors, config=search_config)

        results = cached_engine.search(query, limit=limit)
        return [
            descriptor_payload(
                descriptor,
                detail_level=level,
                surface="mcp",
            )
            for descriptor in results
        ]
