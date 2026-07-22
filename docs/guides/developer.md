# Developer Documentation

## Frequent Commands

| Task              | Command                       |
|:------------------|:----------------------------- |
| Run Tests         | `uv run just test` |
| Lint              | `uv run just lint` |
| Docs (Build)      | `uv run just docs` |
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

The `nomad` CLI installs the system trust store before it creates network
clients. See {repo_file}`src/nomad/truststore.py <src/nomad/truststore.py>` and
{repo_file}`src/nomad/__main__.py <src/nomad/__main__.py>`.
