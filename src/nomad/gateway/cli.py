from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import click
import typer

from ..common.config_errors import ConfigError
from ..common.upstream_errors import UpstreamConnectionError
from ..logging_utils import (
    configure_root_logging,
    more_verbose_log_level,
    parse_log_level,
)
from .config import GatewayConfig
from .server import run_gateway

app = typer.Typer(
    name="nomad-code-mode",
    no_args_is_help=True,
    help="Launch the Nomad MCP code-mode gateway.",
)


TRANSPORT_CHOICES = {"stdio", "streamable-http"}


def _configure_logging(
    level: str, log_file: Path | None = None
) -> logging.Handler | None:
    try:
        numeric_level = parse_log_level(level)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    file_handler = configure_root_logging(
        stderr_level=numeric_level,
        log_file=log_file,
    )
    logging.getLogger("fastmcp.server.context.to_client").setLevel(
        more_verbose_log_level(logging.WARNING)
        if log_file is not None
        else logging.WARNING
    )
    logging.getLogger("httpx").setLevel(
        more_verbose_log_level(logging.WARNING)
        if log_file is not None
        else logging.WARNING
    )
    logging.getLogger("httpcore").setLevel(
        more_verbose_log_level(logging.WARNING)
        if log_file is not None
        else logging.WARNING
    )
    return file_handler


def _normalize_transport(transport: str | None) -> str | None:
    if transport is None:
        return None
    normalized = transport.replace("_", "-")
    if normalized not in TRANSPORT_CHOICES:
        choices = ", ".join(sorted(TRANSPORT_CHOICES))
        raise typer.BadParameter(f"Transport must be one of: {choices}.")
    return normalized


def launch_code_mode(
    config: Path,
    *,
    log_level: str,
    log_file: Path | None,
    transport: str | None,
    host: str | None,
    port: int | None,
) -> None:
    """Load gateway config and run the code-mode gateway."""
    file_handler = _configure_logging(log_level, log_file)
    config_path = config.expanduser()
    try:
        gateway_config = GatewayConfig.from_file(config_path)
    except ConfigError as exc:
        raise typer.BadParameter(
            f"Failed to load gateway config: {exc} (path: '{config_path}')"
        ) from exc

    normalized_transport = _normalize_transport(transport)
    extra_kwargs: dict[str, object] = {}
    if host is not None:
        extra_kwargs["host"] = host
    if port is not None:
        extra_kwargs["port"] = port

    try:
        run_gateway(
            gateway_config,
            transport=normalized_transport,
            log_level=log_level,
            log_file_handler=file_handler,
            **extra_kwargs,
        )
    except UpstreamConnectionError as exc:
        raise click.ClickException(str(exc)) from exc
    except KeyboardInterrupt:
        logging.getLogger("nomad.gateway").info(
            "Gateway interrupted, shutting down.",
        )
    finally:
        if file_handler is not None:
            root = logging.getLogger()
            if file_handler in root.handlers:
                root.removeHandler(file_handler)
            file_handler.close()


@app.command("run")
def run(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Path to the gateway configuration file (YAML, TOML, or JSON).",
        ),
    ],
    transport: Annotated[
        str | None,
        typer.Option(
            "--transport",
            case_sensitive=False,
            help="Optional transport override passed to FastMCP.",
        ),
    ] = None,
    host: Annotated[
        str | None,
        typer.Option(
            "--host",
            help="Optional host override when using network transports.",
        ),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(
            "--port",
            help="Optional port override when using network transports.",
        ),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option(
            "--log-level",
            help="Logging level (default: INFO).",
        ),
    ] = "INFO",
    log_file: Annotated[
        Path | None,
        typer.Option(
            "--log-file",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
            help="Optional append-only JSONL log file.",
        ),
    ] = None,
) -> None:
    """Launch the Nomad MCP code-mode gateway."""

    launch_code_mode(
        config,
        log_level=log_level,
        log_file=log_file,
        transport=transport,
        host=host,
        port=port,
    )
