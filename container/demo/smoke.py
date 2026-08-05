#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer>=0.15"]
# ///
"""Smoke tests for the Nomad demo image and observability Compose stack."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable

import typer

app = typer.Typer(no_args_is_help=True)
POLL_SECONDS = 5
MAX_ATTEMPTS = 60
MIST_CALL = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "mist_models---mist_26p9M_kkgx0omx_qm9",
        "arguments": {"smi": "CCO"},
    },
}


def run(*command: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, check=True, env=env)


def http_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except OSError:
        return 0


def wait_for(description: str, check: Callable[[], bool]) -> None:
    for _ in range(MAX_ATTEMPTS):
        if check():
            return
        time.sleep(POLL_SECONDS)
    typer.echo(f"Timed out waiting for {description}", err=True)
    raise typer.Exit(1)


def call_mist(host: str) -> bool:
    request = urllib.request.Request(
        f"http://{host}:38217/mcp",
        data=json.dumps(MIST_CALL).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            for line in response.read().decode().splitlines():
                if line.startswith("data: "):
                    payload = json.loads(line.removeprefix("data: "))
                    result = payload.get("result", {})
                    return not result.get("isError", False) and bool(
                        result.get("content")
                    )
    except (OSError, json.JSONDecodeError):
        return False
    return False


def verify_nomad(host: str) -> None:
    wait_for(
        "Nomad MCP endpoint", lambda: http_status(f"http://{host}:38217/mcp") == 405
    )
    if not call_mist(host):
        typer.echo("MIST model tool call failed", err=True)
        raise typer.Exit(1)


@app.command()
def image(
    image: str = typer.Option(..., help="Demo image to run."),
    host: str = typer.Option("127.0.0.1", help="Hostname used to probe services."),
) -> None:
    """Start one image and verify Nomad serves an inference request."""
    container = "nomad-demo-smoke"
    try:
        run(
            "docker",
            "run",
            "--detach",
            "--name",
            container,
            "--publish",
            "38217:38217",
            image,
            "serve",
            "--transport=streamable-http",
            "--host=0.0.0.0",
            "--port=38217",
            "/nomad/container/demo/nomad-smoke.yml",
        )
        verify_nomad(host)
    finally:
        subprocess.run(["docker", "logs", container], check=False)
        subprocess.run(["docker", "rm", "--force", container], check=False)


@app.command()
def observability(
    image: str = typer.Option(..., help="Demo image used by the Nomad service."),
    host: str = typer.Option("127.0.0.1", help="Hostname used to probe services."),
    config: str = typer.Option(
        "/nomad/container/demo/nomad-smoke.yml",
        help="Nomad config path in the container.",
    ),
) -> None:
    """Start Compose and verify Nomad plus every observability service."""
    compose = ("docker", "compose", "--file", "container/demo/compose.yml")
    environment = os.environ | {"NOMAD_CONFIG": config, "NOMAD_DEMO_IMAGE": image}
    try:
        run(*compose, "config", "--quiet", env=environment)
        run(*compose, "up", "--detach", "--no-build", env=environment)
        verify_nomad(host)
        for url, status in (
            (f"http://{host}:13133/", 200),
            (f"http://{host}:16686/", 200),
            (f"http://{host}:9090/-/ready", 200),
            (f"http://{host}:3000/api/health", 200),
        ):
            wait_for(url, lambda url=url, status=status: http_status(url) == status)

        def prometheus_scraped_nomad() -> bool:
            try:
                with urllib.request.urlopen(  # noqa: S310
                    f"http://{host}:9090/api/v1/query?query=up%7Bjob%3D%22nomad%22%7D",
                    timeout=10,
                ) as response:
                    result = json.loads(response.read())["data"]["result"]
                    return bool(result) and result[0]["value"][1] == "1"
            except (urllib.error.URLError, json.JSONDecodeError, KeyError):
                return False

        wait_for("Prometheus scrape of Nomad", prometheus_scraped_nomad)
    finally:
        subprocess.run([*compose, "logs"], check=False, env=environment)
        subprocess.run([*compose, "down", "--volumes"], check=False, env=environment)


if __name__ == "__main__":
    app()
