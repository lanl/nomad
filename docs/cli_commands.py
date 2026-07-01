from __future__ import annotations

import typer.main

from nomad.cli import app

nomad = typer.main.get_command(app)


def _register_commands() -> list[str]:
    exported: list[str] = []
    for command_name in sorted(nomad.commands):
        command = nomad.commands[command_name]
        if getattr(command, "hidden", False):
            continue
        export_name = command_name.replace("-", "_")
        globals()[export_name] = command
        exported.append(export_name)
    return exported


__all__ = ["nomad", *_register_commands()]
