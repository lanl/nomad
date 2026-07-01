from __future__ import annotations

import json
import os
import runpy
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _serialize_nomad_tensor(value: Any) -> str | None:
    if value.__class__.__module__.partition(".")[0] != "torch":
        return None

    from nomad.tensor_codac import is_tensor, serialize_tensor

    if not is_tensor(value):
        return None

    return serialize_tensor(value)


def _default_serializer(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if (tensor_payload := _serialize_nomad_tensor(value)) is not None:
        return tensor_payload

    # Serialize Pydantic duck-types using model_dump/model_dump_json
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except Exception:
            try:
                return _default_serializer(model_dump(mode="python"))
            except TypeError:
                try:
                    return _default_serializer(model_dump())
                except Exception:
                    pass
            except Exception:
                pass

    if isinstance(value, (set, frozenset, list)):
        return [_default_serializer(v) for v in value]
    elif isinstance(value, dict):
        return {
            _default_serializer(k): _default_serializer(v) for k, v in value.items()
        }
    return str(value)


def execute_user_script(
    script_path: Path, script_args: Sequence[str] = ()
) -> dict[str, Any]:
    """Execute a user script and return its globals dictionary."""
    previous_argv = sys.argv[:]
    sys.argv = [str(script_path), *script_args]
    try:
        return runpy.run_path(str(script_path), run_name="__main__")
    finally:
        sys.argv = previous_argv


def _sanitize_sys_path(wrappers_root: Path) -> None:
    """Limit module search path to wrappers and standard library roots."""
    resolved_wrappers = wrappers_root.resolve()
    workspace_root = Path.cwd().resolve()
    sanitized: list[str] = []
    base_prefix = Path(sys.base_prefix).resolve()
    prefix = Path(sys.prefix).resolve()
    for entry in sys.path:
        if not entry:
            sanitized.append(entry)
            continue
        path = Path(entry).resolve()
        if path == resolved_wrappers or resolved_wrappers in path.parents:
            sanitized.append(str(path))
            continue
        if path == workspace_root or workspace_root in path.parents:
            sanitized.append(str(path))
            continue
        if base_prefix in path.parents or path == base_prefix:
            sanitized.append(str(path))
            continue
        if prefix in path.parents or path == prefix:
            sanitized.append(str(path))
            continue
        if "site-packages" in path.parts and "ursa" not in path.as_posix():
            sanitized.append(str(path))
    if str(workspace_root) not in sanitized:
        sanitized.insert(0, str(workspace_root))
    sys.path[:] = sanitized


def main(argv: Sequence[str] | None = None) -> int:
    """Run the sandbox child-process entry point."""
    script_path = Path(os.environ["NOMAD_MCP_SCRIPT_PATH"])
    result_path = Path(os.environ["NOMAD_MCP_RESULT_PATH"])
    wrappers_root = Path(os.environ["NOMAD_MCP_WRAPPERS_ROOT"])
    script_args = tuple(argv or ())
    _sanitize_sys_path(wrappers_root)
    payload = None
    try:
        namespace = execute_user_script(script_path, script_args)
        result = namespace.get("RESULT")
        payload = {"result": result}
    except Exception as exc:  # noqa: BLE001
        payload = {"result": {"error": str(exc)}}
        raise
    finally:
        _ = result_path.write_text(
            json.dumps(payload, default=_default_serializer),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
