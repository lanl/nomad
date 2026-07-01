# Deployment Specification

This page provides detailed requirements for deploying SciFMs with Nomad.

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD",
"SHOULD NOT", "RECOMMENDED",  "MAY", and "OPTIONAL" in this document are to be
interpreted as described in [RFC-2119][].

## Model Code and Dependencies

1. Model/server implementation code SHALL live in a separate Python package repository.
   - The package SHALL be pip installable
   - The package SHOULD specify dependencies using a [`pyproject.toml`](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/) file.
2. External model/server packages SHOULD be pinned.
3. Model-specific dependencies SHOULD NOT be added to the root project.
4. Model and tool code SHALL NOT write to stdout; use [Python logging](https://docs.python.org/3/library/logging.html).
5. The Model's' {py:meth}`TorchModuleTool.from_pretrained <nomad.fm_base_tool.TorchModuleTool.from_pretrained>` SHALL do the following:
   - Accept an input signature of `from_pretrained(name_or_path: str)` where `name_or_path` is an absolute path to a model directory.
   - Return a fully initialized model on {py:func}`nomad.fm_base_tool.default_device`
   - Return independent instances of {py:class}`nomad.fm_base_tool.TorchModuleTool` on each invocation.
6. Instances of {py:class}`nomad.fm_base_tool.TorchModuleTool` SHALL only use the accelerator indicated by {py:meth}`TorchModuleTool.device <nomad.fm_base_tool.TorchModuleTool>`;
   multi-accelerator execution within one tool instance is not permitted.

## Model Configuration

1. External model sources are preferred.
2. `name_or_path` SHOULD point to an external source such as Hugging Face,
   ORAS, or a Git/Git LFS repository.
3. If local model directories are used, names SHALL satisfy [MCP tool naming constraints](https://modelcontextprotocol.io/specification/server/tools#tool-names).
4. If local model directories are used, each weight revision SHALL use a distinct directory name.
5. Local model directories SHALL include a model card aligned to {repo_file}`container/model-card.md`.
6. A [SEP-986][] `tool_name` SHOULD be provided if `name_or_path` does not satisfy [SEP-986][].

## Runtime policy

1. Tools SHALL NOT write persistent files.
2. Temporary files MUST be invocation-scoped and use the {py:mod}`tempfile` module.
3. Entries under `fmod_models` MUST be `TorchModuleTool` implementations.
4. Entries under `tools` (non-`fmod_models`) SHALL be CPU-only.
6. A single `TorchModuleTool` instance SHALL use only `TorchModuleTool.device`;
   multi-GPU execution within one tool instance is not permitted.

[RFC-2219]: https://datatracker.ietf.org/doc/html/rfc2119
[SEP-986]: https://modelcontextprotocol.io/seps/986-specify-format-for-tool-names
