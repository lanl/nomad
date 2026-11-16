import asyncio
import json
import logging
import os
import threading
from collections.abc import Sequence
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any, Literal

import click
import typer
from fastmcp import FastMCP
from typer.core import TyperCommand

from ._torch_module_compat import add_torch_module_tool_to_fastmcp
from .common.config_errors import ConfigError
from .common.upstream_errors import UpstreamConnectionError
from .config import ServerConfig
from .export import export as export_command
from .gateway import cli as gateway_cli
from .gateway.config import GatewayConfig
from .gateway.server import CodeModeGateway
from .logging_utils import configure_root_logging, parse_log_level
from .model_cards import ModelCardLocator, register_model_card_tool
from .otel import configure_otel, shutdown_otel
from .tool_search import register_search_tool

LogLevelName = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ServeTransport = Literal["stdio", "http", "streamable-http", "streamable_http"]

app = typer.Typer(
    no_args_is_help=True,
    help=(
        "Serve SciFM-backed MCP tools, export self-contained deployment bundles, "
        "and run scripts through Nomad's code-mode gateway."
    ),
    rich_markup_mode="markdown",
)
app.command(
    "export",
    short_help="Bundle models and rewrite a deployable config.",
)(export_command)

LOGGER = logging.getLogger(__name__)
try:
    _PACKAGE_VERSION = version("nomad-scifm")
except PackageNotFoundError:  # pragma: no cover - local checkout
    _PACKAGE_VERSION = "0.0.0"
_CODE_MODE_EXEC_CONTEXT_SETTINGS = {
    "allow_extra_args": True,
}


def _normalize_serve_transport(transport: str) -> str:
    normalized = transport.replace("_", "-")
    if normalized == "streamable-http":
        return "http"
    return normalized


def _format_visible_devices(manager: Any) -> str:
    accelerator_info = getattr(manager, "accelerator_info", None)
    if accelerator_info:
        formatted: list[str] = []
        for info in accelerator_info:
            device = getattr(info, "device", info)
            name = getattr(info, "name", None)
            if name and name != str(device):
                formatted.append(f"{device} ({name})")
            else:
                formatted.append(str(device))
        return ", ".join(formatted)

    devices = getattr(manager, "devices", None)
    if devices:
        return ", ".join(str(device) for device in devices)
    return "none"


def _register_tools(
    config: ServerConfig,
    server: FastMCP,
    manager: "TorchModelToolManager | None",
    card_locator: ModelCardLocator,
    strict: bool,
    context_dir: Path,
) -> set[str]:
    """Register all tools from config. Returns set of registered fmod model tool names."""
    registered_fmod_names = set()

    # Register regular tools
    for tool in config.tools:
        tool.add_to_fastmcp(server)

    # Register model tools
    for fm_config in config.fmod_models:
        fm_name = fm_config.tool_name or fm_config.name_or_path
        try:
            tool = config.build_tool(fm_config)
            source = fm_config.resolve_source(base_dir=context_dir)

            LOGGER.info("Registering torch model '%s'", fm_name)

            if manager:
                manager.register_tool(tool.name, tool, source=source)
            else:
                add_torch_module_tool_to_fastmcp(server, tool)

            card_locator.register(tool.name or fm_config.name_or_path, source)
            registered_fmod_names.add(tool.name)
        except Exception:
            LOGGER.exception("Failed to load torch model: `%s`", fm_name)
            if strict:
                raise

    if manager:
        manager.add_to_fastmcp(server)

    return registered_fmod_names


def _unregister_tools(
    server: FastMCP,
    manager: "TorchModelToolManager | None",
    card_locator: ModelCardLocator,
    tool_names: set[str],
) -> None:
    """Unregister tools by name."""
    from .common.name_sanitize import sanitize_mcp_name

    # Remove tools from FastMCP's local provider
    sanitized_tool_names = {sanitize_mcp_name(name) for name in tool_names}
    try:
        for name in sanitized_tool_names:
            server.local_provider.remove_tool(name)
            LOGGER.info("Removed tool '%s'", name)
    except Exception:
        LOGGER.exception("Failed to remove tools: %s", tool_names)

    if manager:
        # Remove from manager's internal state (need to use sanitized names)
        for name in tool_names:
            sanitized_name = sanitize_mcp_name(name)
            if sanitized_name in manager._tools:
                state = manager._tools.pop(sanitized_name)
                manager._inflight_by_tool.pop(sanitized_name, None)
                # Offload and clean up
                for slot_idx in list(state.loaded_slots):
                    if slot_idx < len(manager._device_slots):
                        slot = manager._device_slots[slot_idx]
                        if slot.current_tool == sanitized_name:
                            slot.current_tool = None
                            slot.tool = None
                            slot.busy = False
                LOGGER.info("Removed tool '%s' from manager", name)

    # Unregister model cards
    for name in tool_names:
        card_locator.unregister(name)


def _register_specific_tools(
    config: ServerConfig,
    server: FastMCP,
    manager: "TorchModelToolManager | None",
    card_locator: ModelCardLocator,
    strict: bool,
    context_dir: Path,
    tool_names: set[str],
) -> set[str]:
    """Register only specific tools from config that are in tool_names. Returns set of registered fmod model tool names."""
    registered_fmod_names = set()

    # Register regular tools that are in tool_names
    for tool in config.tools:
        if tool.name in tool_names:
            tool.add_to_fastmcp(server)

    # Register model tools that are in tool_names
    for fm_config in config.fmod_models:
        fm_name = fm_config.tool_name or fm_config.name_or_path
        if fm_name in tool_names:
            try:
                tool = config.build_tool(fm_config)
                source = fm_config.resolve_source(base_dir=context_dir)

                LOGGER.info("Registering torch model '%s'", fm_name)

                if manager:
                    manager.register_tool(tool.name, tool, source=source)
                else:
                    add_torch_module_tool_to_fastmcp(server, tool)

                card_locator.register(tool.name or fm_config.name_or_path, source)
                registered_fmod_names.add(tool.name)
            except Exception:
                LOGGER.exception("Failed to load torch model: `%s`", fm_name)
                if strict:
                    raise

    if manager and registered_fmod_names:
        # Only add the newly registered tools to FastMCP, not all tools in manager
        for name in registered_fmod_names:
            if name in manager._tools:
                state = manager._tools[name]
                fast_tool = add_torch_module_tool_to_fastmcp(
                    server,
                    state.tool,
                    invoke=lambda args, tool_name=name: manager.call_tool(tool_name, args),
                )
            else:
                LOGGER.warning("Tool %s not in manager._tools", name)

    return registered_fmod_names


async def _watch_config(
    config_path: Path,
    server: FastMCP,
    manager_ref: list["TorchModelToolManager | None"],
    card_locator: ModelCardLocator,
    use_tool_manager: bool,
    strict: bool,
    interval: float,
    stop_event: threading.Event,
) -> None:
    """Watch config file for changes and hot-swap tools."""
    # Load initial config for diffing
    current_config = ServerConfig.from_file(config_path)
    last_mtime = config_path.stat().st_mtime
    LOGGER.info("Watching config file for changes (interval: %.1fs)", interval)

    while not stop_event.is_set():
        await asyncio.sleep(interval)
        if stop_event.is_set():
            break

        try:
            current_mtime = config_path.stat().st_mtime
        except FileNotFoundError:
            LOGGER.warning("Config file not found: %s", config_path)
            continue

        if current_mtime <= last_mtime:
            continue

        last_mtime = current_mtime
        LOGGER.info("Config file changed, reloading...")
        try:
            current_mtime = config_path.stat().st_mtime
        except FileNotFoundError:
            LOGGER.warning("Config file not found: %s", config_path)
            continue

        if current_mtime <= last_mtime:
            continue

        last_mtime = current_mtime
        LOGGER.info("Config file changed, reloading...")

        try:
            new_config = ServerConfig.from_file(config_path)
        except ConfigError as exc:
            LOGGER.error("Failed to load new config: %s", exc)
            continue
        except Exception as exc:
            LOGGER.exception("Unexpected error loading config")
            continue

        manager = manager_ref[0]

        # Recreate manager if tool_manager config changed
        manager_cfg = new_config.tool_manager
        new_use_manager = use_tool_manager and manager_cfg.enabled

        if new_use_manager != (manager is not None) or (manager is not None and manager_cfg != current_config.tool_manager):
            LOGGER.info("Tool manager config changed, recreating manager")
            if manager:
                await manager.aclose()
            manager = manager_cfg.instantiate() if new_use_manager else None
            manager_ref[0] = manager
            LOGGER.info("Visible devices: %s", _format_visible_devices(manager))

        # Compute diff - detect added, removed, and changed tools
        # For fmod_models, we need to track by tool_name (or name_or_path if no tool_name)
        def _tool_key(fm_config):
            return fm_config.tool_name or fm_config.name_or_path

        # Build maps of tool_key -> config for comparison
        old_fmod_map = {_tool_key(fm): fm for fm in current_config.fmod_models}
        new_fmod_map = {_tool_key(fm): fm for fm in new_config.fmod_models}

        # Build sets of tool names for regular tools
        old_regular_tools = {tool.name for tool in current_config.tools}
        new_regular_tools = {tool.name for tool in new_config.tools}

        # Find removed, added, and changed fmod models
        old_fmod_keys = set(old_fmod_map.keys())
        new_fmod_keys = set(new_fmod_map.keys())

        removed_fmod = old_fmod_keys - new_fmod_keys
        added_fmod = new_fmod_keys - old_fmod_keys
        common_fmod = old_fmod_keys & new_fmod_keys

        # Detect changed fmod models (same name but different config)
        changed_fmod = set()
        for key in common_fmod:
            old_fm = old_fmod_map[key]
            new_fm = new_fmod_map[key]
            # Compare relevant config fields that would require reloading the model
            if (old_fm.model_class != new_fm.model_class or
                old_fm.name_or_path != new_fm.name_or_path or
                old_fm.batch_size != new_fm.batch_size):
                changed_fmod.add(key)

        # For regular tools, only detect added/removed (not changes)
        removed_regular = old_regular_tools - new_regular_tools
        added_regular = new_regular_tools - old_regular_tools

        removed = removed_fmod | removed_regular
        added = added_fmod | added_regular
        # Changed tools need to be removed and re-added
        changed = changed_fmod

        if removed:
            LOGGER.info("Removing tools: %s", removed)
            _unregister_tools(server, manager, card_locator, removed)

        if changed:
            LOGGER.info("Reloading changed tools: %s", changed)
            # Unregister changed tools first
            _unregister_tools(server, manager, card_locator, changed)
            # Then register them with new config
            _register_specific_tools(new_config, server, manager, card_locator, strict, new_config.context_dir, changed)

        if added:
            LOGGER.info("Adding tools: %s", added)
            # Only register the newly added tools, not all tools from new_config
            _register_specific_tools(new_config, server, manager, card_locator, strict, new_config.context_dir, added)

        # Update search tool if config changed
        if new_config.search_tool.expose != current_config.search_tool.expose:
            if new_config.search_tool.expose:
                register_search_tool(server, search_config=new_config.search_tool)
            else:
                try:
                    server.remove_tool("search_tools")
                except Exception:
                    pass

        current_config = new_config  # Update reference for next iteration
        LOGGER.info("Config reload complete")


def run_code_mode_script(
    config_path: Path,
    script_path: Path,
    gateway_log_level: str,
    directory: Path = Path.cwd(),
    script_args: Sequence[str] = (),
) -> dict[str, Any]:
    """Execute a Python script inside the code-mode gateway sandbox."""

    gateway_cli._configure_logging(gateway_log_level)
    try:
        gateway_config = GatewayConfig.from_file(config_path)
    except ConfigError as exc:
        raise typer.BadParameter(
            f"Failed to load gateway config '{config_path}': {exc}"
        ) from exc
    gateway_config.defaults.workspace_root = directory
    gateway_telemetry = getattr(gateway_config, "telemetry", None)
    configure_otel(
        service_name=getattr(gateway_telemetry, "service_name", "nomad-gateway"),
        service_version=_PACKAGE_VERSION,
        enabled=getattr(gateway_telemetry, "enabled", None),
        otlp_endpoint=getattr(gateway_telemetry, "otlp_endpoint", None),
    )

    async def _execute() -> dict[str, Any]:
        async with CodeModeGateway(gateway_config) as gateway:
            result = await gateway.run_script(
                script_path,
                script_args=script_args,
                timeout_seconds=None,
                capture_stdio=False,
            )
            payload = asdict(result)
            payload.pop("stdout", None)
            payload.pop("stderr", None)
            return payload

    try:
        return asyncio.run(_execute())
    except UpstreamConnectionError as exc:
        raise click.ClickException(str(exc)) from exc
    except TimeoutError:
        return {
            "result": {"error": "timeout"},
            "tool_calls": [],
        }
    finally:
        shutdown_otel()


@app.command(
    short_help="Serve Nomad tools over stdio or HTTP.",
)
def serve(
    config: Annotated[
        Path,
        typer.Argument(
            help="Path to the Nomad server config file, usually `nomad.yml`.",
            envvar="NOMAD_SERVE_CONFIG",
        ),
    ],
    log_level: Annotated[
        LogLevelName,
        typer.Option(
            "--log-level",
            help="Minimum log level written to stderr.",
            envvar="NOMAD_SERVE_LOG_LEVEL",
        ),
    ] = "INFO",
    transport: Annotated[
        ServeTransport,
        typer.Option(
            "--transport",
            "-t",
            case_sensitive=False,
            envvar="NOMAD_SERVE_TRANSPORT",
            help=(
                "Server transport. Use `stdio` for local MCP clients or "
                "`http` for network clients."
            ),
        ),
    ] = "stdio",
    use_tool_manager: Annotated[
        bool,
        typer.Option(
            "--tool-manager/--no-tool-manager",
            help=(
                "Enable Nomad's PyTorch tool manager for batching and shared "
                "accelerator scheduling."
            ),
        ),
    ] = True,
    host: Annotated[
        str,
        typer.Option(
            "--host",
            envvar="NOMAD_SERVE_HOST",
            help="Host interface for `http`. Ignored for `stdio`.",
        ),
    ] = "localhost",
    port: Annotated[
        int,
        typer.Option(
            "--port",
            help="Port for `http`. Ignored for `stdio`.",
            envvar="NOMAD_SERVE_PORT",
        ),
    ] = 8000,
    log_file: Annotated[
        Path | None,
        typer.Option(
            "--log-file",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
            help="Optional JSONL file for structured append-only logs.",
            envvar="NOMAD_SERVE_LOG_FILE",
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict/--no-strict",
            help="Fail startup if any model fails to load or register.",
            envvar="NOMAD_SERVE_STRICT",
        ),
    ] = True,
    watch: Annotated[
        bool,
        typer.Option(
            "--watch/--no-watch",
            help="Watch config file for changes and hot-swap tools.",
            envvar="NOMAD_SERVE_WATCH",
        ),
    ] = False,
    watch_interval: Annotated[
        float,
        typer.Option(
            "--watch-interval",
            help="Config file poll interval in seconds.",
            envvar="NOMAD_SERVE_WATCH_INTERVAL",
        ),
    ] = 2.0,
):
    """Start a Nomad MCP server from a config file.

    The config determines which MCP tools, model-backed tools, model cards, and
    optional search endpoints are registered. Use `stdio` for local clients and
    `http` when exposing Nomad over the network.
    """

    transport = _normalize_serve_transport(transport)
    numeric_level = parse_log_level(log_level)
    configure_root_logging(stderr_level=numeric_level, log_file=log_file)
    LOGGER.setLevel(numeric_level)
    try:
        config_obj: ServerConfig = ServerConfig.from_file(config)
    except ConfigError as exc:
        raise click.ClickException(f"Failed to load config: {exc}") from exc
    telemetry = getattr(config_obj, "telemetry", None)
    configure_otel(
        service_name=getattr(telemetry, "service_name", "nomad"),
        service_version=_PACKAGE_VERSION,
        enabled=getattr(telemetry, "enabled", None),
        otlp_endpoint=getattr(telemetry, "otlp_endpoint", None),
    )

    server = FastMCP("nomad", on_duplicate="warn")
    card_locator = ModelCardLocator()
    register_model_card_tool(server, card_locator)

    manager_cfg = config_obj.tool_manager
    use_manager = use_tool_manager and manager_cfg.enabled

    if not manager_cfg.enabled and use_tool_manager:
        LOGGER.info("Tool manager disabled by configuration")

    from .torch_tool_manager import TorchModelToolManager
    manager = manager_cfg.instantiate() if use_manager else None
    LOGGER.info("Visible devices: %s", _format_visible_devices(manager))

    # Set up hot-swapping if requested
    watch_task = None
    manager_ref = [manager]  # Mutable reference for _watch_config

    try:
        # Register initial tools
        registered_fmod_names = _register_tools(config_obj, server, manager, card_locator, strict, config_obj.context_dir)

        if config_obj.search_tool.expose:
            register_search_tool(
                server,
                search_config=config_obj.search_tool,
            )

        watch_thread = None
        watch_stop_event = None

        if watch:
            import threading
            watch_stop_event = threading.Event()

            def run_watch_task():
                asyncio.run(_watch_config(
                    config_path=config,
                    server=server,
                    manager_ref=manager_ref,
                    card_locator=card_locator,
                    use_tool_manager=True,
                    strict=strict,
                    interval=watch_interval,
                    stop_event=watch_stop_event,
                ))

            watch_thread = threading.Thread(target=run_watch_task, daemon=True)
            watch_thread.start()

        try:
            run_kwargs: dict[str, Any] = {
                "log_level": log_level,
                "show_banner": False,
            }
            if transport != "stdio":
                run_kwargs.update(
                    {
                        "host": host,
                        "port": port,
                        "stateless_http": True,
                    }
                )

            server.run(transport=transport, **run_kwargs)
        finally:
            if watch_stop_event:
                watch_stop_event.set()
            if watch_thread:
                watch_thread.join(timeout=5.0)
    finally:
        if manager_ref[0]:
            asyncio.run(manager_ref[0].aclose())
        shutdown_otel()


class CmxCommand(TyperCommand):
    def collect_usage_pieces(self, ctx: click.Context) -> list[str]:
        return [*super().collect_usage_pieces(ctx), "[-- SCRIPT_ARGS...]"]


@app.command(
    "code-mode-exec",
    cls=CmxCommand,
    context_settings=_CODE_MODE_EXEC_CONTEXT_SETTINGS,
    short_help="Run a script within the code-mode sandbox. Alias: cmx",
)
@app.command(
    "cmx",
    cls=CmxCommand,
    hidden=True,
    context_settings=_CODE_MODE_EXEC_CONTEXT_SETTINGS,
)
def code_mode_exec(
    ctx: typer.Context,
    script: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Path to the Python script to execute inside the sandbox.",
        ),
    ],
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
            help="Path to the code-mode gateway config file.",
        ),
    ],
    directory: Annotated[
        Path,
        typer.Option(
            "--directory",
            dir_okay=True,
            file_okay=False,
            resolve_path=True,
            help="Workspace directory exposed to the sandbox.",
            show_default="current directory",
        ),
    ] = Path.cwd(),
    output: Annotated[
        str | None,
        typer.Option(
            "--output",
            "-o",
            help=(
                "Optional destination for the JSON result payload. "
                "Omit to suppress output; use '-' for stdout."
            ),
        ),
    ] = None,
    gateway_log_level: Annotated[
        str,
        typer.Option(
            "--gateway-log-level",
            help="Minimum log level emitted by the temporary gateway process.",
        ),
    ] = logging.getLevelName(logging.WARNING),
) -> None:
    """Run a script within Nomad's code-mode sandbox.

    This is useful for validating a gateway config, testing tool access from a
    script, or capturing a structured JSON result without starting a long-lived
    gateway process. Arguments after ``--`` are forwarded to the `SCRIPT`.
    """

    result = run_code_mode_script(
        config_path=config,
        script_path=script,
        gateway_log_level=gateway_log_level,
        directory=directory,
        script_args=list(ctx.args),
    )
    payload = json.dumps(result, indent=2)

    if output is not None:
        if output == "-":
            typer.echo(payload)
        else:
            output_path = Path(output).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + "\n", encoding="utf-8")


app.command(
    "code-mode",
    short_help="Run the standalone code-mode gateway.",
)(gateway_cli.run)