"""Reusable Sphinx directive for embedded asciinema recordings."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from docutils import nodes
from docutils.parsers.rst import directives
from sphinx.application import Sphinx
from sphinx.util.docutils import SphinxDirective


def _nonnegative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise ValueError(f"negative value; must be positive or zero: {value}")
    return number


class AsciinemaPlayerDirective(SphinxDirective):
    """Render an asciinema webplayer for a ``.cast`` file in the docs tree."""

    required_arguments = 1
    final_argument_whitespace = False
    has_content = False
    option_spec = {
        "autoplay": directives.flag,
        "cols": directives.positive_int,
        "idle-time-limit": _nonnegative_float,
        "loop": directives.flag,
        "poster": directives.unchanged,
        "rows": directives.positive_int,
        "speed": _nonnegative_float,
        "theme": directives.unchanged,
    }

    def run(self) -> list[nodes.Node]:
        source_path = Path(self.state.document.current_source).resolve()
        docs_root = Path(self.env.srcdir).resolve()
        cast_path = (source_path.parent / self.arguments[0]).resolve()

        if cast_path.suffix != ".cast" or not cast_path.is_file():
            raise self.error(f"asciinema recording not found: {cast_path}")

        try:
            cast_source = cast_path.relative_to(docs_root).as_posix()
        except ValueError as error:
            raise self.error(
                "asciinema recordings must be inside the docs tree"
            ) from error

        options = {
            "autoplay": "autoplay" in self.options,
            "cols": self.options.get("cols"),
            "idleTimeLimit": self.options.get("idle-time-limit"),
            "loop": "loop" in self.options,
            "poster": self.options.get("poster"),
            "rows": self.options.get("rows"),
            "speed": self.options.get("speed"),
            "theme": self.options.get("theme"),
        }
        options = {key: value for key, value in options.items() if value is not None}

        markup = (
            '<div class="asciinema-player-embed" '
            f'data-cast-src="{html.escape(cast_source, quote=True)}" '
            f'data-player-options="{html.escape(json.dumps(options), quote=True)}">'
            '<p class="asciinema-player-fallback">'
            f'<a href="{html.escape(self.arguments[0], quote=True)}">'
            "Download the terminal recording</a>.</p></div>"
        )
        return [nodes.raw("", markup, format="html")]


def _copy_recordings(app: Sphinx, exception: Exception | None) -> None:
    if exception is not None or app.builder.format != "html":
        return

    docs_root = Path(app.srcdir)
    output_root = Path(app.outdir)
    for cast_path in docs_root.rglob("*.cast"):
        if output_root in cast_path.parents:
            continue
        relative_path = cast_path.relative_to(docs_root)
        if "_build" in relative_path.parts:
            continue
        destination = output_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cast_path, destination)


def setup(app: Sphinx) -> dict[str, bool]:
    app.add_directive("asciinema-player", AsciinemaPlayerDirective)
    app.connect("build-finished", _copy_recordings)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
