from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from mcp import StdioServerParameters
from mcp.client.session_group import (
    StreamableHttpParameters,
)
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ..common.config_errors import (
    format_validation_error_inline,
    load_config_mapping,
    validate_config_data,
)
from .runtime.constants import DEFAULT_WRAPPERS_PACKAGE


def _validate_server_parameters(
    config: dict,
) -> StdioServerParameters | StreamableHttpParameters:
    if not isinstance(config, dict):
        return config  # type: ignore[return-value]
    transport_hint = config.get("transport")
    payload = {k: v for k, v in config.items() if k != "transport"}
    if transport_hint == "stdio":
        return StdioServerParameters(**payload)
    if transport_hint == "streamable_http":
        return StreamableHttpParameters(**payload)
    if transport_hint is None:
        for candidate in (
            StdioServerParameters,
            StreamableHttpParameters,
        ):
            try:
                return candidate(**payload)
            except ValidationError:
                continue
        msg = (
            "Unable to determine transport for MCP server configuration. "
            "Provide 'transport' with one of: stdio, streamable_http."
        )
        raise ValueError(msg)
    raise ValueError(
        f"Unsupported MCP transport '{transport_hint}' for server '{config}'."
    )


ServerParameters = Annotated[
    StdioServerParameters | StreamableHttpParameters,
    BeforeValidator(_validate_server_parameters),
]


class SandboxLimits(BaseModel):
    """Execution limits applied to sandboxed Python runs."""

    model_config = ConfigDict(from_attributes=True)

    timeout_seconds: float = 30.0
    """Maximum sandbox run time in seconds."""

    stdout_limit: int = 65_536
    """Maximum captured stdout size in bytes."""

    stderr_limit: int = 65_536
    """Maximum captured stderr size in bytes."""

    result_limit: int = 65_536
    """Maximum serialized ``RESULT`` size in bytes."""

    workspace_root: Path | None = Field(
        default_factory=lambda: Path(tempfile.mkdtemp(prefix="nomad_gateway_runs_"))
    )
    """Working directory used for sandboxed script execution."""

    @field_validator("workspace_root", mode="before")
    @classmethod
    def _expand_path(
        cls, value: Path | str | None, info: ValidationInfo
    ) -> Path | None:
        if value is None:
            return None
        path = Path(str(value)).expanduser()
        if not path.is_absolute() and info.context:
            base = info.context.get("base_path")
            if isinstance(base, Path):
                path = (base / path).expanduser()
        if not path.is_absolute():
            path = path.resolve()
        return path


class GatewayDefaults(SandboxLimits):
    """Base defaults merged with per-request overrides."""

    model_config = ConfigDict(from_attributes=True)

    wrappers_package: str = Field(default=DEFAULT_WRAPPERS_PACKAGE)
    """Python package name used for generated MCP tool wrappers."""


class MiddlewareConfig(BaseModel):
    """Generic middleware configuration entry."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    """Middleware implementation name."""

    options: dict[str, Any] = Field(default_factory=dict)
    """Implementation-specific middleware options."""


class AllowlistMiddlewareConfig(MiddlewareConfig):
    """Allow or deny upstream tools by ``server:tool`` glob patterns."""

    kind: Literal["allowlist"] = "allowlist"
    allow: Sequence[str] = Field(default_factory=list)
    """Allowed ``server:tool`` patterns. Empty means allow all not denied."""

    deny: Sequence[str] = Field(default_factory=list)
    """Denied ``server:tool`` patterns."""

    @model_validator(mode="after")
    def _merge_options(self) -> AllowlistMiddlewareConfig:
        merged = dict(self.options)
        merged.setdefault("allow", list(self.allow))
        merged.setdefault("deny", list(self.deny))
        self.options = merged
        return self


class LoggingMiddlewareConfig(MiddlewareConfig):
    """Structured logging middleware configuration."""

    kind: Literal["logging"] = "logging"
    level: Literal["debug", "info", "warning", "error"] = "info"
    """Log level used for upstream tool invocation records."""

    @model_validator(mode="after")
    def _merge_options(self) -> LoggingMiddlewareConfig:
        merged = dict(self.options)
        merged.setdefault("level", self.level)
        self.options = merged
        return self


class RedactionMiddlewareConfig(MiddlewareConfig):
    """Response redaction middleware configuration."""

    kind: Literal["redaction"] = "redaction"
    mode: Literal["basic", "none"] = "basic"
    """Redaction mode for upstream tool responses."""

    @model_validator(mode="after")
    def _merge_options(self) -> RedactionMiddlewareConfig:
        merged = dict(self.options)
        merged.setdefault("mode", self.mode)
        self.options = merged
        return self


class TelemetryConfig(BaseModel):
    """OpenTelemetry exporter configuration for the gateway process."""

    model_config = ConfigDict(from_attributes=True)

    enabled: bool | None = None
    """Enable OTel exporter setup. ``None`` enables it when OTel env vars exist."""

    service_name: str = "nomad-gateway"
    """OpenTelemetry service.name for gateway spans."""

    otlp_endpoint: str | None = None
    """Optional OTLP/gRPC endpoint. Environment variables are used when unset."""


MiddlewareEntry = (
    AllowlistMiddlewareConfig
    | LoggingMiddlewareConfig
    | RedactionMiddlewareConfig
    | MiddlewareConfig
)


class GatewayConfig(BaseModel):
    """Top-level configuration for the MCP gateway."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    servers: dict[str, ServerParameters | dict[str, Any]] = Field(default_factory=dict)
    """Upstream MCP servers keyed by local server ID."""

    defaults: GatewayDefaults = Field(default_factory=GatewayDefaults)
    """Default sandbox runtime options."""

    middleware: Sequence[MiddlewareEntry] = Field(default_factory=list)
    """Middleware entries applied to upstream tool calls."""

    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    """OpenTelemetry exporter settings for gateway tracing."""

    @model_validator(mode="before")
    @classmethod
    def _coerce_servers(cls, values: dict[str, Any] | Any) -> dict[str, Any] | Any:
        if not isinstance(values, dict):
            return values
        raw_servers = values.get("servers", {})
        if not isinstance(raw_servers, dict):
            raise ValueError("'servers' must be a mapping of server names to configs.")
        coerced: dict[str, ServerParameters] = {}
        for name, cfg in raw_servers.items():
            if isinstance(
                cfg,
                (StdioServerParameters, StreamableHttpParameters),
            ):
                coerced[name] = cfg
            else:
                if not isinstance(cfg, dict):
                    raise ValueError(
                        f"Server '{name}' config must be a mapping, got "
                        f"{type(cfg).__name__}."
                    )
                try:
                    coerced[name] = _validate_server_parameters(dict(cfg))
                except ValidationError as exc:
                    raise ValueError(
                        f"Server '{name}' config is invalid: "
                        f"{format_validation_error_inline(exc)}"
                    ) from exc
                except ValueError as exc:
                    raise ValueError(
                        f"Server '{name}' config is invalid: {exc}"
                    ) from exc
        values["servers"] = coerced
        return values

    @model_validator(mode="after")
    def _normalize_paths(self) -> GatewayConfig:
        if self.defaults.workspace_root is not None:
            self.defaults.workspace_root.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def from_file(cls, path: str | Path) -> GatewayConfig:
        """Load a YAML, JSON, or TOML gateway config from disk."""
        path, data = load_config_mapping(
            path,
            supported_suffixes=(".yaml", ".yml", ".json", ".toml"),
            empty_hint="a mapping with a 'servers' section",
        )

        def _validate(data: dict[str, Any]) -> GatewayConfig:
            return cls.model_validate(data, context={"base_path": path.parent})

        return validate_config_data(data, validate=_validate, source=path)


@dataclass(slots=True)
class GatewayRuntimeOptions:
    """Runtime options computed from config and request overrides."""

    timeout_seconds: float | None
    stdout_limit: int
    stderr_limit: int
    result_limit: int
    workspace_root: Path | None
    wrappers_root: Path
    wrappers_package: str
    python_executable: Path

    @classmethod
    def from_defaults_and_overrides(
        cls,
        defaults: GatewayDefaults,
        overrides: dict[str, Any] | None = None,
        *,
        wrappers_root: Path,
    ) -> GatewayRuntimeOptions:
        overrides = overrides or {}
        timeout_source = overrides.get("timeout_seconds", defaults.timeout_seconds)
        timeout_seconds = None if timeout_source is None else float(timeout_source)
        workspace_override = overrides.get("workspace_root")
        if workspace_override is None:
            workspace_root = defaults.workspace_root
        else:
            workspace_root = Path(str(workspace_override)).expanduser().resolve()
        wrappers_root = wrappers_root.expanduser().resolve()
        wrappers_root.mkdir(parents=True, exist_ok=True)

        python_executable = cls._resolve_python_executable(workspace_root, overrides)

        return cls(
            timeout_seconds=timeout_seconds,
            stdout_limit=int(overrides.get("stdout_limit", defaults.stdout_limit)),
            stderr_limit=int(overrides.get("stderr_limit", defaults.stderr_limit)),
            result_limit=int(overrides.get("result_limit", defaults.result_limit)),
            workspace_root=workspace_root,
            wrappers_root=wrappers_root,
            wrappers_package=cast(
                str,
                overrides.get("wrappers_package", defaults.wrappers_package),
            ),
            python_executable=python_executable,
        )

    @staticmethod
    def _detect_workspace_python(workspace_root: Path | None) -> Path | None:
        if workspace_root is None or not workspace_root.exists():
            return None
        venv_root = workspace_root / ".venv"
        candidates: list[Path] = []
        if os.name == "nt":
            scripts_dir = venv_root / "Scripts"
            candidates.extend(
                [
                    scripts_dir / "python.exe",
                    scripts_dir / "python",
                ]
            )
        else:
            bin_dir = venv_root / "bin"
            candidates.extend(
                [
                    bin_dir / "python",
                    bin_dir / "python3",
                ]
            )
        for candidate in candidates:
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return candidate
            except OSError:
                continue
        return None

    @classmethod
    def _resolve_python_executable(
        cls,
        workspace_root: Path | None,
        overrides: dict[str, Any] | None,
    ) -> Path:
        overrides = overrides or {}
        python_override = overrides.get("python_executable")
        if python_override is not None:
            python_path = Path(str(python_override)).expanduser()
        else:
            detected = cls._detect_workspace_python(workspace_root)
            python_path = detected if detected is not None else Path(sys.executable)
        if not python_path.is_absolute():
            python_path = python_path.resolve()
        if not python_path.exists():
            raise FileNotFoundError(f"Python executable '{python_path}' not found")
        return python_path
