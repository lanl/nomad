from __future__ import annotations

import asyncio
from types import ModuleType
from typing import Any

import pytest
import torch
from pydantic import BaseModel, ConfigDict

from nomad import tensor_codac
from nomad.gateway.runtime import client as runtime_client
from nomad.gateway.runtime import deserialization as runtime_deserialization
from nomad.gateway.runtime.wrapper_factory import build_server_module
from nomad.tensor_codac import serialize_tensor
from nomad.well_format import BASE_TENSOR_SCHEMA, Tensor


def _tool_entry(
    identifier: str,
    *,
    output_schema: dict[str, Any] | None = None,
    input_schema: dict[str, Any] | None = None,
    description: str = "Sample tool.",
) -> dict[str, Any]:
    return {
        "identifier": identifier,
        "tool_name": identifier,
        "description": description,
        "schema": {
            "name": identifier,
            "inputSchema": input_schema
            or {
                "type": "object",
                "properties": {},
            },
            "outputSchema": output_schema or {},
        },
    }


def _build_test_module_from_tools(
    tools: list[dict[str, Any]],
    *,
    server: str = "dummy",
    module_name: str = "mcp_tools.dummy",
) -> ModuleType:
    exports = [tool["identifier"] for tool in tools]
    module = ModuleType(module_name)
    build_server_module(
        module,
        {
            "server": server,
            "exports": exports,
            "tools": tools,
        },
    )
    return module


def _build_test_module(*, output_schema: dict[str, Any] | None = None) -> ModuleType:
    return _build_test_module_from_tools(
        [_tool_entry("sample_tool", output_schema=output_schema)]
    )


def _install_payload_stubs(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    def _call_tool_sync(server: str, tool: str, arguments: dict[str, Any]) -> Any:
        assert server == "dummy"
        assert tool == "sample_tool"
        assert arguments == {}
        return payload

    async def _call_tool_async(
        server: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> Any:
        assert server == "dummy"
        assert tool == "sample_tool"
        assert arguments == {}
        return payload

    monkeypatch.setattr(runtime_client, "call_tool_sync", _call_tool_sync)
    monkeypatch.setattr(runtime_client, "call_tool_async", _call_tool_async)


def _install_tool_payload_stubs(
    monkeypatch: pytest.MonkeyPatch,
    payloads: dict[str, Any],
    *,
    server: str = "dummy",
) -> None:
    def _call_tool_sync(
        observed_server: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> Any:
        assert observed_server == server
        assert arguments == {}
        return payloads[tool]

    async def _call_tool_async(
        observed_server: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> Any:
        assert observed_server == server
        assert arguments == {}
        return payloads[tool]

    monkeypatch.setattr(runtime_client, "call_tool_sync", _call_tool_sync)
    monkeypatch.setattr(runtime_client, "call_tool_async", _call_tool_async)


def _invoke_wrapper(module: ModuleType, *, async_mode: bool) -> Any:
    return _invoke_named_wrapper(module, "sample_tool", async_mode=async_mode)


def _invoke_named_wrapper(
    module: ModuleType,
    name: str,
    *,
    async_mode: bool,
) -> Any:
    if async_mode:
        return asyncio.run(getattr(module, f"{name}_async")())
    return getattr(module, name)()


def _encode_tensor(tensor: torch.Tensor) -> str:
    return serialize_tensor(tensor)


def _tensor_schema() -> dict[str, Any]:
    return dict(BASE_TENSOR_SCHEMA)


def _assert_uses_base_tensor_schema(schema: dict[str, Any]) -> None:
    for key, value in BASE_TENSOR_SCHEMA.items():
        assert schema[key] == value


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_wrapper_normalizes_empty_content_blocks_to_none(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    module = _build_test_module()
    _install_payload_stubs(
        monkeypatch,
        {
            "meta": None,
            "isError": False,
            "structuredContent": None,
            "content": [],
        },
    )

    assert _invoke_wrapper(module, async_mode=async_mode) is None


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_wrapper_preserves_all_content_blocks_when_unstructured(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    module = _build_test_module()
    _install_payload_stubs(
        monkeypatch,
        {
            "meta": None,
            "isError": False,
            "structuredContent": None,
            "content": [
                {"type": "text", "text": "alpha"},
                {"type": "text", "text": "beta"},
                {"type": "resource", "uri": "file:///tmp/example.txt"},
            ],
        },
    )

    assert _invoke_wrapper(module, async_mode=async_mode) == [
        "alpha",
        "beta",
        {"type": "resource", "uri": "file:///tmp/example.txt"},
    ]


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_wrapper_preserves_image_and_audio_metadata_in_content_blocks(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    module = _build_test_module()
    _install_payload_stubs(
        monkeypatch,
        {
            "meta": None,
            "isError": False,
            "structuredContent": None,
            "content": [
                {"type": "text", "text": "preview"},
                {
                    "type": "image",
                    "data": "iVBORw0KGgoAAAANSUhEUg==",
                    "mimeType": "image/png",
                },
                {
                    "type": "audio",
                    "data": "UklGRiQAAABXQVZFZm10",
                    "mimeType": "audio/wav",
                },
            ],
        },
    )

    assert _invoke_wrapper(module, async_mode=async_mode) == [
        "preview",
        {
            "type": "image",
            "data": "iVBORw0KGgoAAAANSUhEUg==",
            "mimeType": "image/png",
        },
        {
            "type": "audio",
            "data": "UklGRiQAAABXQVZFZm10",
            "mimeType": "audio/wav",
        },
    ]


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_wrapper_runs_nomad_schema_deserializer_for_nested_result_fields(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    module = _build_test_module(
        output_schema={
            "type": "object",
            "properties": {
                "structuredContent": {
                    "type": "object",
                    "properties": {
                        "result": {
                            "type": "object",
                            "properties": {
                                "tensor": _tensor_schema(),
                                "plain": {"type": "string"},
                                "items": {
                                    "type": "array",
                                    "items": _tensor_schema(),
                                },
                            },
                        }
                    },
                }
            },
        }
    )
    _install_payload_stubs(
        monkeypatch,
        {
            "meta": None,
            "isError": False,
            "structuredContent": {
                "result": {
                    "tensor": "encoded-1",
                    "plain": "leave-me-alone",
                    "items": ["encoded-2", "encoded-3"],
                }
            },
            "content": [],
        },
    )

    def _decode(value: Any, media_type: str) -> Any:
        return {"media_type": media_type, "value": value}

    monkeypatch.setattr(runtime_deserialization, "_deserialize_nomad_media", _decode)

    assert _invoke_wrapper(module, async_mode=async_mode) == {
        "tensor": {
            "media_type": "application/vnd.nomad.tensor",
            "value": "encoded-1",
        },
        "plain": "leave-me-alone",
        "items": [
            {
                "media_type": "application/vnd.nomad.tensor",
                "value": "encoded-2",
            },
            {
                "media_type": "application/vnd.nomad.tensor",
                "value": "encoded-3",
            },
        ],
    }


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_each_wrapper_uses_its_own_output_schema(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    expected = torch.tensor([1, 2, 3], dtype=torch.int16)
    module = _build_test_module_from_tools(
        [
            _tool_entry("plain_tool", output_schema={"type": "string"}),
            _tool_entry("tensor_tool", output_schema=_tensor_schema()),
        ]
    )
    _install_tool_payload_stubs(
        monkeypatch,
        {
            "plain_tool": "not-base64",
            "tensor_tool": _encode_tensor(expected),
        },
    )

    plain = _invoke_named_wrapper(module, "plain_tool", async_mode=async_mode)
    tensor = _invoke_named_wrapper(module, "tensor_tool", async_mode=async_mode)

    assert plain == "not-base64"
    assert isinstance(tensor, torch.Tensor)
    assert tensor.dtype == expected.dtype
    assert torch.equal(tensor, expected)


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_wrapper_resolves_nested_model_json_schema_refs_for_nomad_tensors(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    class TensorLeaf(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        tensor: Tensor

    class Payload(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        item: TensorLeaf
        items: list[TensorLeaf]

    output_schema = Payload.model_json_schema()
    _assert_uses_base_tensor_schema(
        output_schema["$defs"]["TensorLeaf"]["properties"]["tensor"]
    )
    assert output_schema["properties"]["item"] == {"$ref": "#/$defs/TensorLeaf"}
    assert output_schema["properties"]["items"]["items"] == {
        "$ref": "#/$defs/TensorLeaf"
    }

    expected_item = torch.tensor([1, 2], dtype=torch.int16)
    expected_items = [
        torch.tensor([3, 4], dtype=torch.int16),
        torch.tensor([5, 6], dtype=torch.int16),
    ]
    module = _build_test_module(output_schema=output_schema)
    _install_payload_stubs(
        monkeypatch,
        {
            "item": {"tensor": _encode_tensor(expected_item)},
            "items": [
                {"tensor": _encode_tensor(expected_items[0])},
                {"tensor": _encode_tensor(expected_items[1])},
            ],
        },
    )

    result = _invoke_wrapper(module, async_mode=async_mode)

    assert isinstance(result["item"]["tensor"], torch.Tensor)
    assert torch.equal(result["item"]["tensor"], expected_item)
    assert len(result["items"]) == len(expected_items)
    assert all(
        torch.equal(item["tensor"], expected)
        for item, expected in zip(result["items"], expected_items, strict=True)
    )


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_wrapper_prepares_ref_schema_before_runtime_deserialization(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    class TensorLeaf(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        tensor: Tensor

    class Payload(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        items: list[TensorLeaf]

    expected = [
        torch.tensor([1, 2], dtype=torch.int16),
        torch.tensor([3, 4], dtype=torch.int16),
    ]
    module = _build_test_module(output_schema=Payload.model_json_schema())
    _install_payload_stubs(
        monkeypatch,
        {
            "items": [
                {"tensor": _encode_tensor(expected[0])},
                {"tensor": _encode_tensor(expected[1])},
            ],
        },
    )

    def _raise_if_runtime_resolves_refs(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("schema refs should be prepared before wrapper calls")

    monkeypatch.setattr(
        runtime_deserialization,
        "resolve_schema_fragment",
        _raise_if_runtime_resolves_refs,
    )

    result = _invoke_wrapper(module, async_mode=async_mode)

    assert all(
        torch.equal(item["tensor"], tensor)
        for item, tensor in zip(result["items"], expected, strict=True)
    )


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_wrapper_prefers_media_bearing_union_branch(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    class PlainPayload(BaseModel):
        label: str

    class TensorPayload(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        tensor: Tensor

    class OutputPayload(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        item: PlainPayload | TensorPayload

    expected = torch.tensor([1, 2], dtype=torch.int16)
    module = _build_test_module(output_schema=OutputPayload.model_json_schema())
    _install_payload_stubs(
        monkeypatch,
        {
            "item": {
                "tensor": _encode_tensor(expected),
            },
        },
    )

    result = _invoke_wrapper(module, async_mode=async_mode)

    assert isinstance(result["item"]["tensor"], torch.Tensor)
    assert torch.equal(result["item"]["tensor"], expected)


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_wrapper_resolves_ref_based_structured_content_schema_from_model_json_schema(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    class TensorLeaf(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        tensor: Tensor

    class WrappedResult(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        result: TensorLeaf

    class OutputEnvelope(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        structuredContent: WrappedResult

    output_schema = OutputEnvelope.model_json_schema()
    _assert_uses_base_tensor_schema(
        output_schema["$defs"]["TensorLeaf"]["properties"]["tensor"]
    )
    assert output_schema["properties"]["structuredContent"] == {
        "$ref": "#/$defs/WrappedResult"
    }
    assert output_schema["$defs"]["WrappedResult"]["properties"]["result"] == {
        "$ref": "#/$defs/TensorLeaf"
    }

    expected = torch.tensor([[1, 2], [3, 4]], dtype=torch.int16)
    module = _build_test_module(output_schema=output_schema)
    _install_payload_stubs(
        monkeypatch,
        {
            "meta": None,
            "isError": False,
            "structuredContent": {
                "result": {
                    "tensor": _encode_tensor(expected),
                }
            },
            "content": [],
        },
    )

    result = _invoke_wrapper(module, async_mode=async_mode)

    assert isinstance(result["tensor"], torch.Tensor)
    assert result["tensor"].dtype == expected.dtype
    assert torch.equal(result["tensor"], expected)


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_wrapper_decodes_nomad_tensor_by_default(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    expected = torch.tensor([[1, 2], [3, 4]], dtype=torch.int16)
    module = _build_test_module(
        output_schema={
            "type": "object",
            "properties": {
                "structuredContent": {
                    "type": "object",
                    "properties": {"result": _tensor_schema()},
                }
            },
        }
    )
    _install_payload_stubs(
        monkeypatch,
        {
            "meta": None,
            "isError": False,
            "structuredContent": {"result": _encode_tensor(expected)},
            "content": [],
        },
    )

    result = _invoke_wrapper(module, async_mode=async_mode)

    assert isinstance(result, torch.Tensor)
    assert result.dtype == expected.dtype
    assert torch.equal(result, expected)


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_model_validate_accepts_wrapper_output_with_deserialized_tensor(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    expected = torch.tensor([[1, 2], [3, 4]], dtype=torch.int16)

    class TensorEnvelope(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        payload: Tensor

    module = _build_test_module(
        output_schema={
            "type": "object",
            "properties": {
                "structuredContent": {
                    "type": "object",
                    "properties": {
                        "result": {
                            "type": "object",
                            "properties": {"payload": _tensor_schema()},
                            "required": ["payload"],
                        }
                    },
                }
            },
        }
    )
    _install_payload_stubs(
        monkeypatch,
        {
            "meta": None,
            "isError": False,
            "structuredContent": {"result": {"payload": _encode_tensor(expected)}},
            "content": [],
        },
    )

    out = _invoke_wrapper(module, async_mode=async_mode)
    restored = TensorEnvelope.model_validate(out)

    assert isinstance(out["payload"], torch.Tensor)
    assert torch.equal(out["payload"], expected)
    assert restored.payload.dtype == expected.dtype
    assert torch.equal(restored.payload, expected)


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_wrapper_raises_when_nomad_tensor_dependencies_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    async_mode: bool,
):
    module = _build_test_module(
        output_schema={
            "type": "object",
            "properties": {
                "structuredContent": {
                    "type": "object",
                    "properties": {"result": _tensor_schema()},
                }
            },
        }
    )
    _install_payload_stubs(
        monkeypatch,
        {
            "meta": None,
            "isError": False,
            "structuredContent": {"result": "deadbeef"},
            "content": [],
        },
    )

    real_import_module = tensor_codac.importlib.import_module

    def _missing_zstandard(name: str) -> Any:
        if name == "zstandard":
            raise ModuleNotFoundError("No module named 'zstandard'")
        return real_import_module(name)

    monkeypatch.setattr(
        tensor_codac.importlib,
        "import_module",
        _missing_zstandard,
    )

    with pytest.raises(
        RuntimeError,
        match=r"Nomad tensor support requires runtime dependencies to be installed: zstandard",
    ):
        _invoke_wrapper(module, async_mode=async_mode)
