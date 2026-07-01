from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .env import deep_interp_env


class ConfigError(ValueError):
    """Raised when a Nomad config file cannot be loaded or validated."""


def load_config_mapping(
    path: str | Path,
    *,
    supported_suffixes: Sequence[str],
    empty_hint: str,
) -> tuple[Path, dict[str, Any]]:
    """Load a config file into a mapping with actionable parse errors."""
    path = Path(path).expanduser()
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    if path.is_dir():
        raise ConfigError(f"Config path is a directory, not a file: {path}")

    suffix = path.suffix.lower()
    supported = {item.lower() for item in supported_suffixes}
    if suffix not in supported:
        suffix_label = path.suffix or "<none>"
        choices = ", ".join(supported_suffixes)
        raise ConfigError(
            f"Unsupported config format '{suffix_label}' for '{path}'. Use {choices}."
        )

    data = _parse_config_file(path, suffix)
    if data is None:
        raise ConfigError(f"Config '{path}' is empty; expected {empty_hint}.")
    if not isinstance(data, dict):
        raise ConfigError(
            f"Config root in '{path}' must be a mapping/object, got "
            f"{type(data).__name__}."
        )
    return path, data


def validate_config_data[T](
    data: dict[str, Any],
    *,
    validate: Callable[[dict[str, Any]], T],
    source: Path | None = None,
) -> T:
    """Apply env interpolation and model validation with shared error formatting."""
    try:
        data = deep_interp_env(data)
        return validate(data)
    except KeyError as exc:
        name = exc.args[0] if exc.args else str(exc)
        raise ConfigError(
            f"Environment variable '{name}' is referenced but not set."
        ) from exc
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from exc
    except OSError as exc:
        source_text = f" '{source}'" if source is not None else ""
        raise ConfigError(
            f"Unable to prepare paths from config{source_text}: {exc.strerror or exc}"
        ) from exc


def format_validation_error(exc: ValidationError) -> str:
    """Format pydantic validation errors as actionable config diagnostics."""
    lines = ["Config schema validation failed. Fix the following issue(s):"]
    for error in exc.errors():
        lines.append(f"- {_format_error(error)}")
    return "\n".join(lines)


def format_validation_error_inline(exc: ValidationError) -> str:
    """Format validation errors for embedding in a larger diagnostic."""
    return "; ".join(_format_error(error) for error in exc.errors())


def _parse_config_file(path: Path, suffix: str) -> Any:
    if suffix in {".yaml", ".yml"}:
        try:
            with path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ConfigError(
                f"YAML syntax error in config '{path}': {_format_yaml_error(exc)}"
            ) from exc
        except OSError as exc:
            raise ConfigError(
                f"Unable to read config '{path}': {exc.strerror or exc}"
            ) from exc

    if suffix == ".json":
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"JSON syntax error in config '{path}' at line {exc.lineno}, "
                f"column {exc.colno}: {exc.msg}"
            ) from exc
        except OSError as exc:
            raise ConfigError(
                f"Unable to read config '{path}': {exc.strerror or exc}"
            ) from exc

    if suffix == ".toml":
        try:
            import tomllib  # type: ignore[attr-defined]
        except ImportError:  # pragma: no cover - Python <3.11
            import tomli as tomllib  # type: ignore[no-redef]
        try:
            with path.open("rb") as fh:
                return tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"TOML syntax error in config '{path}': {exc}") from exc
        except OSError as exc:
            raise ConfigError(
                f"Unable to read config '{path}': {exc.strerror or exc}"
            ) from exc

    raise AssertionError(f"Unsupported suffix reached parser: {suffix}")


def _format_yaml_error(exc: yaml.YAMLError) -> str:
    problem = getattr(exc, "problem", None) or str(exc)
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return str(problem)
    return f"line {mark.line + 1}, column {mark.column + 1}: {problem}"


def _format_error(error: dict[str, Any]) -> str:
    loc = _format_loc(error.get("loc", ()))
    error_type = str(error.get("type", ""))
    message = str(error.get("msg", "Invalid value"))
    ctx = error.get("ctx") if isinstance(error.get("ctx"), dict) else {}
    input_value = error.get("input", None)
    input_text = _format_input(input_value)

    if error_type == "missing":
        return f"Missing required field `{loc}`."
    if error_type == "extra_forbidden":
        return f"Unknown field `{loc}`; remove it or check the field name."
    if error_type == "list_type":
        return f"`{loc}` must be a list{input_text}."
    if error_type in {"dict_type", "mapping_type", "model_type"}:
        return f"`{loc}` must be a mapping/object{input_text}."
    if error_type in _TYPE_NAMES:
        return f"`{loc}` must be {_TYPE_NAMES[error_type]}{input_text}."
    if error_type == "literal_error":
        expected = ctx.get("expected")
        if expected is not None:
            return f"`{loc}` must be one of {expected}{input_text}."
    if error_type == "greater_than_equal":
        return f"`{loc}` must be greater than or equal to {ctx.get('ge')}{input_text}."
    if error_type == "less_than_equal":
        return f"`{loc}` must be less than or equal to {ctx.get('le')}{input_text}."
    if error_type == "greater_than":
        return f"`{loc}` must be greater than {ctx.get('gt')}{input_text}."
    if error_type == "less_than":
        return f"`{loc}` must be less than {ctx.get('lt')}{input_text}."

    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    if message.startswith("Server '"):
        return f"{message}."
    return f"`{loc}` is invalid: {message}{input_text}."


def _format_loc(loc: Sequence[Any]) -> str:
    if not loc:
        return "<root>"
    parts: list[str] = []
    for part in loc:
        if isinstance(part, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{part}]"
            else:
                parts.append(f"[{part}]")
        else:
            parts.append(str(part))
    return ".".join(parts)


def _format_input(value: Any) -> str:
    if value is None:
        return ""
    text = repr(value)
    if len(text) > 120:
        text = f"{text[:117]}..."
    return f" (got {text})"


_TYPE_NAMES = {
    "string_type": "a string",
    "int_type": "an integer",
    "bool_type": "a boolean",
    "float_type": "a number",
    "path_type": "a filesystem path",
}
