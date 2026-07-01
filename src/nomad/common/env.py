from __future__ import annotations

import re
from os import environ
from typing import Any

ENV_SUB_REGEX = re.compile(r"\${(?P<env>\w+)(?::(?P<default>.+))?}")


def deep_interp_env(x: dict[str, Any] | str | Any):
    """Recursively interpolate environment variables in mappings or strings."""
    if isinstance(x, dict):
        return {k: deep_interp_env(v) for k, v in x.items()}
    if isinstance(x, list):
        return [deep_interp_env(v) for v in x]
    if isinstance(x, str):
        return interpolate_env(x)
    return x


def interpolate_env(value: str) -> str:
    """
    Interpolate environment variables in a string.

    Supported patterns:
        ${VAR}
            → value of VAR if set, otherwise empty string.
        ${VAR:DEFAULT}
            → value of VAR if set, otherwise DEFAULT.
    """

    def _replace(match: re.Match[str]) -> str:
        groups = match.groupdict(None)
        default = groups["default"]
        key = groups["env"]
        assert key is not None, "ENV_SUB_REGEX should not match if env is none"
        if (v := environ.get(key)) is not None:
            return v
        elif default is not None:
            return default
        else:
            raise KeyError(f"Env. variable '{key}' is not set and no default was given")

    return ENV_SUB_REGEX.sub(_replace, value)


__all__ = ["deep_interp_env", "interpolate_env"]
