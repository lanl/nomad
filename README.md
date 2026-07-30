<p align="center">
  <img src="https://raw.githubusercontent.com/lanl/nomad/main/assets/icon.png" alt="Nomad icon" width="96">
</p>

# Nomad

Nomad connects AI agents to scientific foundation models (SciFMs). It serves
PyTorch-backed scientific tools over the [Model Context Protocol
(MCP)](https://modelcontextprotocol.io), and it can also expose MCP tools as
typed Python callables inside a code-execution sandbox.

[Documentation site](https://lanl.github.io/nomad) |
[Getting started](https://lanl.github.io/nomad/guides/getting-started.html) |
[Model builder guide](https://lanl.github.io/nomad/guides/model-builder.html) |
[Reference](https://lanl.github.io/nomad/reference/index.html)

## What Nomad does

- Serve SciFMs and regular Python tools over MCP with `nomad serve`.
- Load model artifacts from Hugging Face, local storage, or Git/Git LFS.
- Proxy MCP servers into a Python sandbox with `nomad code-mode` and
  `nomad code-mode-exec`.
- Support both local workflows and hosting models on remote GPU servers.

## Install

Nomad requires Python 3.12 or newer.

For CLI use:

```shell
uv tool install --python 3.13 nomad-scifm

nomad --help
```

For development from a checkout:

```shell
uv sync --all-groups
uv run nomad --help
```

## Quick start

Launch the example server directly in MCP Inspector using stdio:

```shell
npx @modelcontextprotocol/inspector -- \
  uv run --directory container/demo \
  nomad serve nomad.yml
```

For the HTTP workflow and matching [URSA](https://github.com/lanl/ursa) configs, see
[Starting a Nomad server](https://lanl.github.io/nomad/guides/getting-started.html#starting-a-nomad-server)
and
[Connect to a hosted Nomad server](https://lanl.github.io/nomad/guides/getting-started.html#connect-to-a-hosted-nomad-server).

## Start here by task

| If you want to... | Start here |
| --- | --- |
| Connect to a running Nomad server | [Getting started](https://lanl.github.io/nomad/guides/getting-started.html) |
| Host a new SciFM | [Model builder guide](https://lanl.github.io/nomad/guides/model-builder.html) |
| Use Nomad for Inference | [Nomad inference notebook](https://lanl.github.io/nomad/guides/nomad_inference.html) |
| Browse CLI, config, and API docs | [Reference](https://lanl.github.io/nomad/reference/index.html) |
| Run the demo deployment | [Deployments](https://lanl.github.io/nomad/deployments/index.html) |
| Work on Nomad itself | [Developer docs](https://lanl.github.io/nomad/guides/developer.html) |

## Development

Useful commands from a repo checkout:

```shell
uv run --only-group lint just lint
uv run --group dev just test
uv run --no-dev --group docs just docs
uv run --no-dev --group docs just docs-pdf
```

## Notice of Copyright Assertion (O5119)

© 2026. Triad National Security, LLC. All rights reserved.

This program was produced under U.S. Government contract 89233218CNA000001 for
Los Alamos National Laboratory (LANL), which is operated by Triad National
Security, LLC for the U.S. Department of Energy/National Nuclear Security
Administration. All rights in the program are reserved by Triad National
Security, LLC, and the U.S. Department of Energy/National Nuclear Security
Administration. The Government is granted for itself and others acting on its
behalf a nonexclusive, paid-up, irrevocable worldwide license in this material
to reproduce, prepare. derivative works, distribute copies to the public,
perform publicly and display publicly, and to permit others to do so.
