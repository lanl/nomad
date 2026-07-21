---
name: connect-scifm-to-nomad
description: Guide an agent and model builder through connecting a scientific foundation model (SciFM) to Nomad. Use when helping a user work through Nomad's Model Builder guide for a Hugging Face, ORAS, Git/LFS, or local PyTorch scientific model; inspect model inputs and outputs; make code pip-installable; implement a nomad.fm_base_tool.TorchModuleTool adapter; choose schemas for PDE, molecular, genomics, crystal/materials, representation, embedding, masked-token, or grid-based SciFMs; validate the server; or complete a model card.
---

# Connect SciFM To Nomad

This skill is an agent workflow for helping a model builder work through the
Nomad Model Builder docs. Do not duplicate those docs from memory. Use the
published docs as the source of truth for Nomad mechanics, and use this skill
for collaboration, scientific interface discovery, domain pitfalls, and
validation with the user.

## Source Docs

Read the relevant published docs before making implementation decisions:

- Model Builder guide: `https://lanl.github.io/nomad/guides/model-builder.html`
- Deployment specification: `https://lanl.github.io/nomad/deployments/deployment-spec.html`
- Config reference: `https://lanl.github.io/nomad/reference/config.html`
- `TorchModuleTool` API: `https://lanl.github.io/nomad/reference/api-torch-module-tool.html`
- `WellFormat` API: `https://lanl.github.io/nomad/reference/api-well-format.html`
- Model card template source: `https://github.com/lanl/nomad/blob/main/container/model-card.md`

If available, `https://lanl.github.io/nomad/llms.txt` is useful for retrieval,
but prefer the HTML docs or source links when details differ.

## Workflow

1. Build context with the user.
   - Inspect the model card, source code, configs, examples, and existing inference scripts before proposing an interface.
   - Treat the user or model builder as the scientific authority. Infer what you can, then confirm units, normalization, input format, output format, valid ranges, time semantics, accepted formats, and what outputs should mean.
   - Do not assume the user knows Nomad, packaging, or the model codebase. Explain each proposed change in terms of the scientific workflow it supports.

2. Make a shared plan from the Model Builder guide.
   - Map the user's repo onto the guide's required pieces: importable package, model artifact or `name_or_path`, model card, and `nomad.yml` entry.
   - If the model code is not pip-installable or dependencies are not documented, propose a small prep plan before writing the adapter: identify the importable package layout, add or update packaging/dependency files, preserve existing entry points, and add a minimal import test.
   - Keep the user in the driver's seat for prep work. Explain why packaging changes are needed, ask before broad refactors, and prefer helping the user set up source control and a branch before using ad hoc backups.
   - Treat packaging cleanup as a separate phase from the Nomad connector when possible. After each prep step, verify the original model workflow still runs before moving on.

3. Design the scientific tool interface.
   - Decide with the user what one tool call should mean scientifically.
   - Prefer small JSON-friendly inputs and outputs when they can faithfully represent the query and result.
   - Use `WellFormat`, `AutoRegressiveInput`, or tensor fields only when the model genuinely needs structured field, rollout, grid, trajectory, or tensor-heavy data.
   - Add ordinary helper tools when they support the scientific workflow, such as lookup, validation, conversion, or search helpers. Register those as `tools` using dotted function paths; reserve `fmod_models` for `TorchModuleTool` model adapters.
   - For PDE models, read `references/pde-scfm.md`.
   - For molecular models, read `references/molecular-scfm.md`.
   - For genomics, crystal/materials, embedding, masked-token, or other representation models, read `references/representation-scfm.md`.

4. Implement by following the docs.
   - Use the Model Builder guide for package layout, `TorchModuleTool` method responsibilities, `fmod_models` versus `tools`, `name_or_path`, and validation commands.
   - Put the Nomad adapter in the model/server package or a small connector package, not in Nomad's root package.
   - Do not rely on arbitrary `fmod_models` keys reaching `from_pretrained`; follow the current documented config behavior. In current Nomad, `from_pretrained(str(resolved_source))` is called without config kwargs, then supported overrides such as `tool_name` and `batch_size` are applied to the returned tool.
   - Do not add mock, fake, dry-run, or debug model switches to production adapter code. Use real loaders in production; use test fixtures or test doubles only inside tests.
   - If the adapter needs model-specific settings, encode them in the model artifact/config, environment variables, or a small wrapper package; document that choice in the model card.

5. Complete the model card with the user.
   - Nomad's model-card tool always reads `README.md` from the resolved model source, such as Hugging Face, Git, ORAS, or a local model directory. Ensure the model artifact or repo contains that runtime model card.
   - Use the canonical template only as an authoring guide for that `README.md`: `https://github.com/lanl/nomad/blob/main/container/model-card.md`.
   - In a full Nomad checkout, `references/model-card-template.md` is a symlink to that template. If the skill is installed standalone and the symlink is missing or broken, use the GitHub URL instead. Do not treat the skill's template reference as the model card Nomad will serve.
   - Work with the user to describe architecture, input/output schema, training data, intended use, limitations, risks, license, citation, and a minimal inference snippet.
   - Convert an existing model card to match the template when possible, or use it as is if explicitly directed.
   - Include units with predicted outputs whenever possible. If units are unavailable or outputs are unitless, normalized, logits, probabilities, or embeddings, say that explicitly.

6. Validate collaboratively.
   - Read `references/testing-guide.md` for the testing and GPU-marker workflow.
   - First run agent-side checks: package import, schema tests, preprocessing/postprocessing tests, real-model integration tests when available, and Nomad server execution using the surfaces recommended by the Model Builder guide.
   - Then create user-review artifacts. For PDEs and fields, show input frames, predicted frames, deltas, and scalar summaries. For sequence or molecular models, report representative predictions, scores, validity checks, and units.
   - Ask the user to confirm the outputs are scientifically plausible. Treat unexpected predictions as possible interface, preprocessing, unit, or normalization bugs until checked.
