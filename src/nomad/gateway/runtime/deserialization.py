from __future__ import annotations

from typing import Any

NOMAD_MEDIA_TYPE_PREFIX = "application/vnd.nomad."
NOMAD_TENSOR_MEDIA_TYPE = "application/vnd.nomad.tensor"
_HAS_NOMAD_MEDIA_KEY = "__nomad_has_media"
_SCHEMA_TYPES_KEY = "__nomad_schema_types"


def _deserialize_nomad_media(value: Any, media_type: str) -> Any:
    if media_type != NOMAD_TENSOR_MEDIA_TYPE:
        return value

    if not isinstance(value, str):
        raise TypeError("Nomad tensor deserialization expected a base64 string payload")

    from nomad.tensor_codac import deserialize_tensor

    return deserialize_tensor(value)


def _resolve_local_ref(ref: str, root_schema: Any) -> Any:
    if not isinstance(root_schema, dict):
        return None

    if ref == "#":
        return root_schema

    if not ref.startswith("#/"):
        return None

    current: Any = root_schema
    for token in ref[2:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]

    return current


def resolve_schema_fragment(schema: Any, *, root_schema: Any | None = None) -> Any:
    if not isinstance(schema, dict):
        return schema

    if root_schema is None:
        root_schema = schema

    resolved: Any = schema
    seen_refs: set[str] = set()

    while isinstance(resolved, dict):
        ref = resolved.get("$ref")
        if not isinstance(ref, str) or ref in seen_refs:
            return resolved

        seen_refs.add(ref)
        target = _resolve_local_ref(ref, root_schema)
        if not isinstance(target, dict):
            return resolved

        if len(resolved) == 1:
            resolved = target
            continue

        merged = dict(target)
        for key, value in resolved.items():
            if key != "$ref":
                merged[key] = value
        resolved = merged

    return resolved


def _is_nomad_media_schema(schema: dict[str, Any]) -> bool:
    media_type = schema.get("contentMediaType")
    return isinstance(media_type, str) and media_type.startswith(
        NOMAD_MEDIA_TYPE_PREFIX
    )


def _schema_types(schema: dict[str, Any]) -> set[str]:
    prepared_types = schema.get(_SCHEMA_TYPES_KEY)
    if isinstance(prepared_types, set):
        return prepared_types

    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return {schema_type}
    if isinstance(schema_type, list):
        return {item for item in schema_type if isinstance(item, str)}
    return set()


def prepare_deserialization_schema(
    schema: Any,
    *,
    root_schema: Any | None = None,
    _memo: dict[int, dict[str, Any]] | None = None,
) -> Any:
    """Resolve schema references once and mark branches that can deserialize."""
    if not isinstance(schema, dict):
        return schema

    if root_schema is None:
        root_schema = schema
    if _memo is None:
        _memo = {}

    resolved = resolve_schema_fragment(schema, root_schema=root_schema)
    if not isinstance(resolved, dict):
        return resolved

    memo_key = id(resolved)
    if memo_key in _memo:
        return _memo[memo_key]

    prepared = dict(resolved)
    _memo[memo_key] = prepared

    has_nomad_media = _is_nomad_media_schema(prepared)

    for key in ("anyOf", "oneOf"):
        options = prepared.get(key)
        if not isinstance(options, list):
            continue
        prepared_options = [
            prepare_deserialization_schema(
                option,
                root_schema=root_schema,
                _memo=_memo,
            )
            for option in options
        ]
        prepared[key] = prepared_options
        has_nomad_media = has_nomad_media or any(
            isinstance(option, dict) and bool(option.get(_HAS_NOMAD_MEDIA_KEY))
            for option in prepared_options
        )

    properties = prepared.get("properties")
    if isinstance(properties, dict):
        prepared_properties = {
            key: prepare_deserialization_schema(
                value,
                root_schema=root_schema,
                _memo=_memo,
            )
            for key, value in properties.items()
        }
        prepared["properties"] = prepared_properties
        has_nomad_media = has_nomad_media or any(
            isinstance(value, dict) and bool(value.get(_HAS_NOMAD_MEDIA_KEY))
            for value in prepared_properties.values()
        )

    items = prepared.get("items")
    if items is not None:
        prepared_items = prepare_deserialization_schema(
            items,
            root_schema=root_schema,
            _memo=_memo,
        )
        prepared["items"] = prepared_items
        has_nomad_media = has_nomad_media or (
            isinstance(prepared_items, dict)
            and bool(prepared_items.get(_HAS_NOMAD_MEDIA_KEY))
        )

    additional = prepared.get("additionalProperties")
    if isinstance(additional, dict):
        prepared_additional = prepare_deserialization_schema(
            additional,
            root_schema=root_schema,
            _memo=_memo,
        )
        prepared["additionalProperties"] = prepared_additional
        has_nomad_media = has_nomad_media or (
            isinstance(prepared_additional, dict)
            and bool(prepared_additional.get(_HAS_NOMAD_MEDIA_KEY))
        )

    prepared[_SCHEMA_TYPES_KEY] = _schema_types(prepared)
    prepared[_HAS_NOMAD_MEDIA_KEY] = has_nomad_media
    return prepared


def _pick_matching_schema_option(
    value: Any,
    options: list[Any],
) -> dict[str, Any] | None:
    typed_options = [option for option in options if isinstance(option, dict)]
    if not typed_options:
        return None

    if value is None:
        for option in typed_options:
            if "null" in _schema_types(option):
                return option
        return None

    def _matches_type(option: dict[str, Any], schema_type: str) -> bool:
        return schema_type in _schema_types(option)

    type_checks = (
        ("object", dict),
        ("array", list),
        ("string", str),
        ("integer", int),
        ("number", (int, float)),
        ("boolean", bool),
    )
    for schema_type, python_type in type_checks:
        if schema_type in {"integer", "number"} and isinstance(value, bool):
            continue
        if isinstance(value, python_type):
            matching = [
                option for option in typed_options if _matches_type(option, schema_type)
            ]
            for option in matching:
                if option.get(_HAS_NOMAD_MEDIA_KEY):
                    return option
            if matching:
                return matching[0]

    for option in typed_options:
        if option.get(_HAS_NOMAD_MEDIA_KEY):
            return option
    for option in typed_options:
        if (
            "properties" in option
            or "items" in option
            or "additionalProperties" in option
        ):
            return option
    return typed_options[0]


def deserialize_output_value(
    value: Any,
    schema: Any,
    *,
    root_schema: Any | None = None,
) -> Any:
    """Recursively apply schema-guided runtime deserialization."""
    if value is None or not isinstance(schema, dict):
        return value

    if _HAS_NOMAD_MEDIA_KEY not in schema:
        schema = prepare_deserialization_schema(schema, root_schema=root_schema)
    if not isinstance(schema, dict):
        return value
    if not schema.get(_HAS_NOMAD_MEDIA_KEY):
        return value

    media_type = schema.get("contentMediaType")
    if _is_nomad_media_schema(schema):
        return _deserialize_nomad_media(value, media_type)

    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if not isinstance(options, list):
            continue
        option = _pick_matching_schema_option(value, options)
        if option is not None:
            return deserialize_output_value(value, option)
        return value

    properties = schema.get("properties")
    if isinstance(value, dict) and isinstance(properties, dict):
        additional = schema.get("additionalProperties")
        normalized = {}
        for key, item in value.items():
            child_schema = (
                properties.get(key, additional)
                if isinstance(additional, dict)
                else properties.get(key)
            )
            if isinstance(child_schema, dict) and child_schema.get(
                _HAS_NOMAD_MEDIA_KEY
            ):
                normalized[key] = deserialize_output_value(item, child_schema)
            else:
                normalized[key] = item
        return normalized

    items = schema.get("items")
    if isinstance(value, list) and items is not None:
        if not isinstance(items, dict) or not items.get(_HAS_NOMAD_MEDIA_KEY):
            return value
        return [deserialize_output_value(item, items) for item in value]

    additional = schema.get("additionalProperties")
    if isinstance(value, dict) and isinstance(additional, dict):
        if not additional.get(_HAS_NOMAD_MEDIA_KEY):
            return value
        return {
            key: deserialize_output_value(item, additional)
            for key, item in value.items()
        }

    return value
