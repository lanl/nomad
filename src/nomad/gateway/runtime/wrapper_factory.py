from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from .deserialization import (
    deserialize_output_value,
    prepare_deserialization_schema,
    resolve_schema_fragment,
)

TYPE_MAP: dict[str, Any] = {
    "integer": int,
    "number": float,
    "string": str,
    "boolean": bool,
    "array": list[Any],
    "object": dict[str, Any],
}


@dataclass(frozen=True)
class ToolParameter:
    """Parameter metadata derived from an MCP tool input schema."""

    name: str
    annotation: Any
    optional: bool
    description: str


def _annotation_from_schema(schema: dict[str, Any]) -> tuple[Any, bool]:
    if not isinstance(schema, dict):
        return Any, False

    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list) and options:
            annotations: list[Any] = []
            allows_null = False
            for option in options:
                ann, option_allows_null = _annotation_from_schema(option)
                if ann is type(None):
                    allows_null = True
                    continue
                annotations.append(ann)
                allows_null = allows_null or option_allows_null
            if not annotations:
                return Any, True
            if len(annotations) == 1:
                return annotations[0], allows_null or True
            return Any, allows_null

    schema_type = schema.get("type")
    allows_null = False
    if isinstance(schema_type, list):
        allows_null = "null" in schema_type
        non_null = [t for t in schema_type if t != "null"]
        if len(non_null) == 1:
            schema_type = non_null[0]
        elif not non_null:
            return type(None), True
        else:
            return Any, allows_null

    if schema_type == "null":
        return type(None), True

    if schema_type is None and "enum" in schema:
        schema_type = "string"

    annotation = TYPE_MAP.get(schema_type, Any)
    return annotation, allows_null


def _maybe_optional(annotation: Any, is_optional: bool) -> Any:
    if not is_optional:
        return annotation
    return annotation | None  # type: ignore[valid-type]


def _annotation_to_string(annotation: Any) -> str:
    if annotation is type(None):
        return "None"
    if getattr(annotation, "__module__", "") == "typing":
        return repr(annotation).replace("typing.", "")
    if isinstance(annotation, type):
        return annotation.__name__
    return repr(annotation)


def _build_docstring(
    summary: str,
    parameters: list[ToolParameter],
    return_annotation: Any,
    return_description: str,
) -> str:
    lines = [summary.strip() or "Invoke tool via the gateway."]
    if parameters:
        lines.append("")
        lines.append("Args:")
        for param in parameters:
            annotation_str = _annotation_to_string(param.annotation)
            suffix = ", optional" if param.optional else ""
            description = param.description or "See server documentation."
            lines.append(f"    {param.name} ({annotation_str}{suffix}): {description}")
    lines.append("")
    lines.append("Returns:")
    lines.append(
        f"    {_annotation_to_string(return_annotation)}: "
        f"{return_description.strip() or 'Raw MCP call result.'}"
    )
    return "\n".join(lines)


def _prepare_arguments(
    bound: inspect.BoundArguments,
    parameters: list[ToolParameter],
) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    for param in parameters:
        value = bound.arguments[param.name]
        if param.optional and value is None:
            continue
        arguments[param.name] = value
    return arguments


def _build_parameters(input_schema: dict[str, Any]) -> list[ToolParameter]:
    properties = input_schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required = set(input_schema.get("required", []))

    parameters: list[ToolParameter] = []
    for name, prop_schema in properties.items():
        annotation, allows_null = _annotation_from_schema(prop_schema)
        optional = name not in required or allows_null
        annotation = _maybe_optional(annotation, optional or allows_null)
        description = prop_schema.get("description", "")
        parameters.append(
            ToolParameter(
                name=name,
                annotation=annotation,
                optional=optional,
                description=description,
            )
        )
    return parameters


def _normalized_return_annotation(output_schema: dict[str, Any]) -> Any:
    raw_return_annotation, return_allows_null = _annotation_from_schema(output_schema)
    if return_allows_null:
        raw_return_annotation = _maybe_optional(raw_return_annotation, True)

    def _wrap_optional(annotation: Any, allows: bool) -> Any:
        return _maybe_optional(annotation, True) if allows else annotation

    properties = output_schema.get("properties")
    if not isinstance(properties, dict):
        return raw_return_annotation

    if output_schema.get("x-fastmcp-wrap-result") and "result" in properties:
        ann, allows = _annotation_from_schema(properties["result"])
        return _wrap_optional(ann, allows)

    if "structuredContent" in properties:
        structured_schema = properties["structuredContent"]
        ann, allows = _annotation_from_schema(structured_schema)
        candidate = _wrap_optional(ann, allows)

        if isinstance(structured_schema, dict) and isinstance(
            structured_schema.get("properties"), dict
        ):
            sc_props = structured_schema["properties"]
            if "result" in sc_props and len(sc_props) == 1:
                result_ann, result_allows = _annotation_from_schema(sc_props["result"])
                return _wrap_optional(result_ann, result_allows)
        return candidate

    return raw_return_annotation


def _normalized_output_schema(output_schema: dict[str, Any]) -> dict[str, Any]:
    root_schema = output_schema
    schema = resolve_schema_fragment(output_schema, root_schema=root_schema)
    if not isinstance(schema, dict):
        return output_schema

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema

    if schema.get("x-fastmcp-wrap-result") and isinstance(
        properties.get("result"), dict
    ):
        return properties["result"]

    structured_schema = properties.get("structuredContent")
    if not isinstance(structured_schema, dict):
        return schema

    structured_schema = resolve_schema_fragment(
        structured_schema,
        root_schema=root_schema,
    )
    if not isinstance(structured_schema, dict):
        return schema

    structured_properties = structured_schema.get("properties")
    if not isinstance(structured_properties, dict):
        return structured_schema

    if "result" in structured_properties and len(structured_properties) == 1:
        result_schema = structured_properties["result"]
        if isinstance(result_schema, dict):
            return result_schema

    return structured_schema


def _normalize_content_entry(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return entry

    if entry.get("type") in {"image", "audio"} and "data" in entry:
        return entry

    if "data" in entry:
        data = entry["data"]
        if isinstance(data, dict) and "result" in data and len(data) == 1:
            return data["result"]
        return data

    if "text" in entry:
        return entry["text"]

    return entry


def _normalize_content_blocks(content: list[Any]) -> Any:
    if not content:
        return None

    normalized = [_normalize_content_entry(entry) for entry in content]
    if len(normalized) == 1:
        return normalized[0]
    return normalized


def build_tool_signature(schema: dict[str, Any]) -> inspect.Signature:
    """Build a Python call signature from an MCP tool schema."""
    input_schema = schema.get("inputSchema") or {}
    if not isinstance(input_schema, dict):
        input_schema = {}
    parameters = _build_parameters(input_schema)

    output_schema = schema.get("outputSchema") or {}
    if not isinstance(output_schema, dict):
        output_schema = {}
    return_annotation = _normalized_return_annotation(output_schema)

    sig_parameters: list[inspect.Parameter] = []
    for param in parameters:
        default = None if param.optional else inspect.Parameter.empty
        sig_parameters.append(
            inspect.Parameter(
                param.name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=param.annotation,
            )
        )

    return inspect.Signature(
        parameters=sig_parameters,
        return_annotation=return_annotation,
    )


def build_server_module(module: ModuleType, spec: dict[str, Any]) -> None:
    """Populate ``module`` with sync and async wrappers for one MCP server."""
    from . import client as _runtime_client  # local import to avoid cycle

    server = spec.get("server", "unknown")
    tools = spec.get("tools", [])
    exports = spec.get("exports", [])

    module.__doc__ = f"Auto-generated wrappers for MCP server '{server}'."
    module.__all__ = []

    for tool_entry in tools:
        identifier = tool_entry["identifier"]
        tool_name = tool_entry["tool_name"]
        schema = tool_entry.get("schema") or {}
        summary = tool_entry.get("description") or schema.get("description", "")

        input_schema = schema.get("inputSchema") or {}
        parameters = _build_parameters(input_schema)
        output_schema = schema.get("outputSchema") or {}
        normalized_return_annotation = _normalized_return_annotation(output_schema)
        normalized_output_schema = _normalized_output_schema(output_schema)
        deserialization_schema = prepare_deserialization_schema(
            normalized_output_schema,
            root_schema=output_schema,
        )
        return_description = output_schema.get("description", "")

        docstring = _build_docstring(
            summary=summary or f"Invoke '{tool_name}' via the gateway.",
            parameters=parameters,
            return_annotation=normalized_return_annotation,
            return_description=return_description
            or "Normalized tool output extracted from the MCP response.",
        )

        annotations: dict[str, Any] = {}
        for param in parameters:
            annotations[param.name] = param.annotation

        signature = build_tool_signature(schema)
        param_copy = list(parameters)
        annotation_copy = dict(annotations)
        tool_id = tool_name

        def _normalize_payload(
            payload: Any,
            __deserialization_schema=deserialization_schema,
        ) -> Any:
            normalized = payload
            if isinstance(payload, dict):
                structured = payload.get("structuredContent")
                if structured is not None:
                    if isinstance(structured, dict):
                        if "result" in structured and len(structured) == 1:
                            normalized = structured["result"]
                        else:
                            normalized = structured
                    else:
                        normalized = structured
                else:
                    content = payload.get("content")
                    if isinstance(content, list):
                        normalized = _normalize_content_blocks(content)

            return deserialize_output_value(normalized, __deserialization_schema)

        def _sync_wrapper(
            *args,
            __signature=signature,
            __parameters=param_copy,
            __server=server,
            __tool=tool_id,
            __normalize_payload=_normalize_payload,
            **kwargs,
        ):
            bound = __signature.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = _prepare_arguments(bound, __parameters)
            payload = _runtime_client.call_tool_sync(__server, __tool, arguments)
            return __normalize_payload(payload)

        async def _async_wrapper(
            *args,
            __signature=signature,
            __parameters=param_copy,
            __server=server,
            __tool=tool_id,
            __normalize_payload=_normalize_payload,
            **kwargs,
        ):
            bound = __signature.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = _prepare_arguments(bound, __parameters)
            payload = await _runtime_client.call_tool_async(__server, __tool, arguments)
            return __normalize_payload(payload)

        _sync_wrapper.__name__ = identifier
        _async_wrapper.__name__ = f"{identifier}_async"
        _sync_wrapper.__qualname__ = identifier
        _async_wrapper.__qualname__ = f"{identifier}_async"
        _sync_wrapper.__doc__ = docstring
        _async_wrapper.__doc__ = docstring
        _sync_wrapper.__annotations__ = dict(annotation_copy)
        _async_wrapper.__annotations__ = dict(annotation_copy)
        _sync_wrapper.__annotations__["return"] = normalized_return_annotation
        _async_wrapper.__annotations__["return"] = normalized_return_annotation
        _sync_wrapper.__signature__ = signature
        _async_wrapper.__signature__ = signature
        _sync_wrapper.__module__ = module.__name__
        _async_wrapper.__module__ = module.__name__

        module.__dict__[identifier] = _sync_wrapper
        module.__dict__[f"{identifier}_async"] = _async_wrapper
        module.__all__.append(identifier)
        module.__all__.append(f"{identifier}_async")

    if exports:
        ordered = []
        for name in exports:
            if name in module.__dict__ and name not in ordered:
                ordered.append(name)
            async_name = f"{name}_async"
            if async_name in module.__dict__ and async_name not in ordered:
                ordered.append(async_name)
        if ordered:
            module.__all__ = ordered
