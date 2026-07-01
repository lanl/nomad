from __future__ import annotations

import base64
import importlib
import io
from typing import Any

BASE_TENSOR_SCHEMA = {
    "type": "string",
    "contentEncoding": "base64",
    "contentMediaType": "application/vnd.nomad.tensor",
    "description": "Serialized and compressed tensor data",
}


def load_runtime_deps(*deps: str) -> tuple[Any, ...]:
    missing: list[str] = []
    loaded: list[Any] = []

    for dep in deps:
        try:
            loaded.append(importlib.import_module(dep))
        except ModuleNotFoundError:
            missing.append(dep)

    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(
            "Nomad tensor support requires runtime dependencies to be installed: "
            f"{missing_list}"
        )

    return tuple(loaded)


def is_tensor(value: Any) -> bool:
    (torch,) = load_runtime_deps("torch")
    return isinstance(value, torch.Tensor)


def serialize_tensor(tensor: Any) -> str:
    torch, zstandard = load_runtime_deps("torch", "zstandard")
    buffer = io.BytesIO()
    torch.save(tensor.detach().cpu(), buffer)
    payload = zstandard.compress(buffer.getvalue())
    return base64.b64encode(payload).decode("ascii")


def deserialize_tensor(tensor_b64: str) -> Any:
    torch, zstandard = load_runtime_deps("torch", "zstandard")
    payload = base64.b64decode(tensor_b64)
    payload = zstandard.decompress(payload)
    buffer = io.BytesIO(payload)
    tensor = torch.load(buffer, map_location="cpu", weights_only=True)
    if not isinstance(tensor, torch.Tensor):
        raise TypeError("Nomad tensor payload did not decode to a torch.Tensor")
    return tensor
