# Developer Documentation

## Frequent Commands

| Task              | Command                       |
|:------------------|:----------------------------- |
| Run Tests         | `uv run just test` |
| Lint              | `uv run just lint` |
| Docs (Build)      | `uv run just docs` |
| Docs (PDF)        | `uv run --no-dev --group docs just docs-pdf` |
| Docs (Auto-build) | `uv run just docs --live`       |
| Docs (Notebook)   | `uv run --group docs jupyter lab path/to/notebook.ipynb` |

## Embed terminal recordings

Put an asciinema `.cast` file anywhere under `docs/`, next to the page that uses
it when practical, then embed it with the reusable MyST directive:

````markdown
```{asciinema-player} demo.cast
:cols: 80
:rows: 24
:speed: 2
:idle-time-limit: 1
:poster: npt:0:03
:theme: asciinema
```
````

The directive also accepts `:autoplay:` and `:loop:` flags. The docs build
copies recordings into the same relative location in the HTML output and loads
the pinned asciinema webplayer once for the whole site.

## TLS

Nomad verifies secure connections using certificates trusted by the operating
system. Install any organization-specific certificate authorities in the
system trust store before connecting to private model registries, repositories,
or MCP servers. For containers, see
[Passing Custom CA Certificates to the Image](../deployments/guide.md#passing-custom-ca-certificates-to-the-image).
