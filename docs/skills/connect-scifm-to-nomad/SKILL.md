---
name: connect-scifm-to-nomad
description: Connect scientific foundation models (SciFMs) to Nomad as MCP tools. Use when Codex needs to package a Hugging Face, ORAS, Git/LFS, or local PyTorch scientific model for Nomad; implement a nomad.fm_base_tool.TorchModuleTool adapter; choose WellFormat/Tensor/JSON schemas for model inputs and outputs; add fmod_models entries to nomad.yml; validate nomad serve or code-mode-exec; write a model card; or prepare a PDE, rollout, neural-operator, molecular-property, SMILES, chemistry, genomics, crystal/materials, representation, embedding, masked-token, or grid-based SciFM integration.
---

# Connect SciFM To Nomad

## Workflow

1. Inspect the model source and model card.
   - Identify the loading API, required source files, dependencies, input shape, output shape, physical fields, time-step semantics, and license.
   - Prefer a stable external `name_or_path` such as `hf://org/model`, `oras://...`, or `git+...`; use local paths only for development.
   - If model files live in a repository subdirectory, include the fragment in `name_or_path`, for example `hf://org/model#ckpt` or `oras://registry/name:tag#weights/model`.
   - If the user names a concrete model family or URL, inspect that source before designing the adapter.
   - Treat the user or model builder as the scientific authority. Infer inputs and outputs from code first, then confirm units, normalization, input format, output format, valid ranges, time semantics, accepted formats, and what outputs should mean.

2. Choose the Nomad-facing tool contract.
   - Use plain Pydantic fields for small JSON-friendly inputs and outputs.
   - Use `nomad.well_format.Tensor` for direct tensor fields.
   - Use `nomad.well_format.WellFormat` for PDE, rollout, grid, field, or trajectory data.
   - Use `AutoRegressiveInput` only when the model actually advances an initial state by a duration/step count; do not force it onto single-step operator, field-completion, or time-conditioned prediction models.
   - Use small domain strings plus structured outputs for sequence-like models such as SMILES, DNA, SCOPE, FASTA, SELFIES, formulas, or masked-token inputs.
   - For PDE models, read `references/pde-scfm.md` before writing code.
   - For molecular models, read `references/molecular-scfm.md` before writing code.
   - For genomics, crystal/materials, embedding, masked-token, or other representation models, read `references/representation-scfm.md` before writing code.
   - Plan the public interface with the user before hardening it. Do not assume the user knows Nomad or the model codebase; explain choices in terms of the scientific inputs they will provide and the predictions they will inspect.

3. Implement a pip-installable model package.
   - Prefer adding the Nomad adapter to the model builder's own repository, package, or server package. If a third party is building the connector, keep the adapter as a small pip-installable wrapper around the upstream model.
   - Review whether the model repo is ready for inference integration: package metadata, import paths, dependency declarations, weight/config discovery, sample data, and a documented inference entry point.
   - If the model code is not pip-installable or dependencies are not documented, propose a small prep plan before writing the adapter: identify the importable package layout, add or update `pyproject.toml` or `requirements.txt`, preserve existing entry points, and add a minimal import test.
   - Keep the user in the driver's seat for prep work. Explain why packaging changes are needed, ask before broad refactors, and help the user create a reversible checkpoint. Prefer helping the user set up source control and a branch before using ad hoc backups.
   - Treat packaging cleanup as a separate phase from the Nomad connector when possible. After each prep step, work with the user to verify the original model workflow still runs before moving on to integration.
   - Put the Nomad adapter in the model/server package or connector package, not in Nomad's root package.
   - Make the adapter importable by dotted path, for example `my_pkg.mcp.MyModelTool`.
   - Subclass `nomad.fm_base_tool.TorchModuleTool`.
   - Implement `from_pretrained`, `preprocess`, optional `_forward`, and `postprocess`.
   - Put heavyweight loading in `from_pretrained`, not `__init__`.
   - Move tensors to `self.device` in `preprocess`; return CPU tensors or Pydantic outputs from `postprocess`.
   - Yield one output object per input item from `postprocess`.
   - Use logging instead of `print`; do not write persistent files.

4. Configure Nomad.
   - Add `TorchModuleTool` adapters under `fmod_models`, not `tools`.
   - Add regular CPU-only helper functions under `tools`.
   - Provide `tool_name` when the model source name is not a good MCP tool name.
   - Keep `batch_size` conservative for large outputs.
   - Do not rely on arbitrary keys under `fmod_models` being passed to `from_pretrained`. Nomad's current config path resolves `name_or_path`, calls `from_pretrained(resolved_source)`, then applies supported overrides such as `tool_name` and `batch_size`.
   - If the adapter needs model-specific settings, encode them in the model artifact/config, environment variables, or a small wrapper package; document that choice in the model card.
   - Do not add mock, fake, or debug model switches to production adapter code. Use real loaders in production; use test fixtures or test doubles only inside tests.

   ```yaml
   fmod_models:
     - model_class: my_pkg.mcp.MyModelTool
       name_or_path: hf://my-org/my-model
       tool_name: my-model
       batch_size: 1
   ```

5. Add or update the model card.
   - Start from `references/model-card-template.md`.
   - In this repository, that path is a symlink to the canonical template. If a skill packaging surface does not expose symlinks, use `https://github.com/lanl/nomad/blob/main/container/model-card.md`.
   - Keep deployment details concise, but include architecture, input/output schema, training data, intended use, limitations, risks, license, citation, and a minimal inference snippet.
   - Work with the user to fill in model descriptions, intended use, and limitations in language a downstream scientist can understand. The user may already have a model card. Work with them to convert it to match the template when possible, or use it as is if explicitly directed.
   - Include units with predicted outputs whenever possible. If units are unavailable or model outputs are unitless/normalized/logits, say that explicitly.
   - The upstream template also lives at `https://github.com/lanl/nomad/blob/main/container/model-card.md`.

6. Validate in layers.
   - Check the package import from the environment that will run Nomad:

     ```bash
     uv run python -c "from my_pkg.mcp import MyModelTool; print(MyModelTool)"
     ```

   - Start Nomad:

     ```bash
     nomad serve --transport streamable-http --host localhost --port 8000 path/to/nomad.yml
     ```

   - Use MCP Inspector for JSON-friendly tools.
   - Use `nomad code-mode-exec` for `WellFormat` or tensor-heavy tools because hand-writing encoded tensor payloads is error-prone.
   - Read `references/testing-guide.md` for the testing guide. Use pytest markers to separate CPU/package tests from integration tests that genuinely require GPUs or large model weights.
   - Exercise the model with representative inputs before asking for user review. For PDEs and fields, produce visualizations or compact reports of input frames, predicted frames, deltas, and scalar summaries. For sequence or molecular models, report representative predictions, scores, validity checks, and units.
   - Iterate with the user until they confirm the outputs are scientifically plausible. Treat unexpected predictions as a possible interface, preprocessing, unit, or normalization bug until checked.

## Published Nomad Docs

Prefer published documentation over local repository paths when helping someone use Nomad from another project:

- Main docs: `https://lanl.github.io/nomad/`
- Model Builder guide: `https://lanl.github.io/nomad/guides/model-builder.html`
- Deployment specification: `https://lanl.github.io/nomad/deployments/deployment-spec.html`
- Config reference: `https://lanl.github.io/nomad/reference/config.html`
- `TorchModuleTool` API: `https://lanl.github.io/nomad/reference/api-torch-module-tool.html`
- `WellFormat` API: `https://lanl.github.io/nomad/reference/api-well-format.html`
- Model card template source: `https://github.com/lanl/nomad/blob/main/container/model-card.md`

If available, `https://lanl.github.io/nomad/llms.txt` is useful for quick retrieval, but prefer the HTML pages or source links when details differ or the LLM text omits sections.
