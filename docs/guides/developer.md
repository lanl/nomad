# Developer Documentation

## Frequent Commands

| Task              | Command                       |
|:------------------|:----------------------------- |
| Run Tests         | `uv run just test` |
| Lint              | `uv run just lint` |
| Docs (Build)      | `uv run just docs` |
| Docs (Auto-build) | `uv run just docs --live`       |
| Docs (Notebook)   | `uv run --group docs jupyter lab path/to/notebook.ipynb` |

## TLS

The `nomad` CLI installs the system trust store before it creates network
clients. See {repo_file}`src/nomad/truststore.py <src/nomad/truststore.py>` and
{repo_file}`src/nomad/__main__.py <src/nomad/__main__.py>`.
