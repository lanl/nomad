# Nomad: Connecting AI Agents to SciFMs

**Nomad** is a lightweight runtime for serving scientific foundation models
(SciFMs) to AI agents.

It focuses on two things:

- **Serve models at scale.** Run *N* PyTorch models across *M* GPUs through a
unified inference interface. Nomad handles loading, scheduling, batching, and
packaging, whether your weights live on Hugging Face, local storage, or in
Git/Git LFS.

- **Code-mode execution.** Nomad exposes MCP tools as typed Python callables
inside a sandbox, so agents can call and compose them efficiently within an
existing execution environment.

Built on the [**Model Context Protocol (MCP)**](https://modelcontextprotocol.io),
Nomad is agent-agnostic and designed for both HPC and local inference. It makes
SciFMs easier to package, deploy, and integrate into real agent workflows.


## Start Here

- **Connect to a running server**:
  {doc}`Getting Started </guides/getting-started>` shows how to launch
  `nomad serve`, connect from MCP Inspector or Ursa, and run scripts with
  `nomad code-mode-exec`.
- **Host a new model**:
  {doc}`Model Builder </guides/model-builder>` explains how to package a
  {py:class}`nomad.fm_base_tool.TorchModuleTool`, point Nomad at model weights,
  and validate the resulting MCP tool.
- **Operate a deployment**:
  {doc}`Deployment Guide </deployments/guide>` and
  {doc}`Deployment Specification </deployments/deployment-spec>` cover the
  container workflow and deployment requirements.
- **Work on Nomad itself**:
  {doc}`Developer Documentation </guides/developer>` collects contributor
  commands, docs tasks, and environment notes.

## Key References

- {doc}`CLI Reference </reference/cli>`
- {doc}`Configuration Guide </reference/config>`
- {doc}`Configuration Reference </reference/api-config>`
- {doc}`Code-Mode Gateway Reference </reference/api-gateway>`
- {doc}`Tool Discovery and Model Cards Reference </reference/api-tools>`
- {doc}`Tool Manager Reference </reference/api-tool-manager>`
- {doc}`TorchModuleTool Reference </reference/api-torch-module-tool>`
- {doc}`Well Format Reference </reference/api-well-format>`

## Core Concepts

- **Nomad server**:
  `nomad serve` loads regular Python tools and PyTorch-backed model tools from
  config and exposes them over MCP.
- **Code-mode gateway**:
  `nomad code-mode` proxies MCP servers into a sandbox where tools can be
  imported from `mcp_tools.<server>`.
- **Artifact sources**:
  model weights can come from local paths, Hugging Face, Git/Git LFS
  repositories, or ORAS artifacts.
- **Scientific state exchange**:
  {py:class}`nomad.well_format.WellFormat` defines a common schema for gridded
  scientific state across Python objects, JSON/MCP payloads, and HDF5 files.

```{toctree}
:hidden:
:maxdepth: 2
:caption: Sections

guides/index
reference/index
deployments/index
changelog
```
