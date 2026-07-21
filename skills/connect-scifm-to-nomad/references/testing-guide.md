# Testing Guide

Use this reference when validating a Nomad SciFM adapter. The goal is to prove
the connector imports, serves, runs the real model when requested, and produces
scientifically reviewable outputs.

## Contents

- Test Layers
- Production Code Rule
- User-Visible Scientific Validation
- GPU And Scheduler Guidance
- Nomad Execution Checks

## Test Layers

Use pytest for the adapter package and mark tests by the resources they need:

- Fast package tests: import the adapter, validate schemas, check config
  parsing, and exercise pure preprocessing/postprocessing helpers with small
  fixtures. These tests should not load real weights or require GPUs.
- Integration tests: load a real local or cached model artifact and run one
  small inference path. Mark with `@pytest.mark.integration`.
- GPU tests: run only tests that truly need CUDA, large weights, or production
  batch/device behavior. Mark with `@pytest.mark.gpu`, and optionally combine
  with `@pytest.mark.integration`.
- Server tests: start `nomad serve` or use the Nomad execution path and call the
  MCP tool with representative inputs.

Example markers:

```python
import pytest


@pytest.mark.integration
@pytest.mark.gpu
def test_real_model_predicts_on_cuda(sample_input):
    ...
```

Register markers in `pytest.ini` or `pyproject.toml` so pytest documents them:

```toml
[tool.pytest.ini_options]
markers = [
  "integration: loads real artifacts or exercises an external runtime",
  "gpu: requires a GPU or CUDA-enabled model execution",
]
```

Run CPU-safe tests by default:

```bash
pytest -m "not gpu and not integration"
```

Run real-model checks explicitly:

```bash
pytest -m "integration"
pytest -m "gpu"
```

> If the user already has a robust testing framework, integrate testing the TorchModuleTool into the existing framework. The above tags should not be used to replace what may already exist.

## Production Code Rule

Do not add mock, fake, dry-run, or debug-model flags to production adapter code.
Production adapter paths should either load the configured real model or fail
with a clear setup error.

If a large model makes tests slow, place test doubles in test files or test-only
fixtures. Those fixtures may validate schema and package behavior, but they
must not be exposed through `fmod_models`, documented as scientific inference,
or reachable through the production MCP tool.

## User-Visible Scientific Validation

Before claiming the connector works, run at least one representative inference
and create evidence the user can inspect:

- PDE, rollout, and field models: show input frames, predicted frames, deltas,
  channel summaries, units, coordinate assumptions, and time-step information.
- Molecular/property models: show input molecule strings or graphs, predicted
  properties, units, confidence/probability/logit meaning, and validity or
  canonicalization notes.
- Representation, sequence, genomics, and crystal models: show the accepted
  input representation, tokenization/truncation behavior, output embedding
  size or generated candidates, and any scores or validity checks.

Ask the user to confirm that the inputs, outputs, units, and prediction behavior
match how the SciFM should be invoked. If they disagree, treat that feedback as
part of validation and revise the adapter, preprocessing, output schema, or
model card.

## GPU And Scheduler Guidance

On shared clusters, do not run real inference or GPU tests on login/head nodes.
Run only CPU-safe package tests there. Submit tests marked `gpu` through the
site scheduler or run them in a GPU CI/runtime.

For Slurm, use a small checked-in or documented batch script that activates the
already-prepared environment and runs pytest markers:

```bash
#!/usr/bin/env bash
#SBATCH --job-name=nomad-scfm-tests
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --time=00:20:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --output=/absolute/path/logs/%x-%j.out
#SBATCH --error=/absolute/path/logs/%x-%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "${REPO_ROOT}"
mkdir -p logs .hf-cache .torch-cache

export HF_HOME="${REPO_ROOT}/.hf-cache"
export TORCH_HOME="${REPO_ROOT}/.torch-cache"
export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"

pytest -m "gpu"
```

Avoid package installs and model downloads inside scheduled jobs unless the
cluster explicitly supports networked compute nodes. Prepare the environment and
cache model artifacts before submitting, or document the cache location and the
reason a download is required.

Record the command, job id, selected markers, model artifact path, and stdout or
stderr log paths. If a GPU job is pending, failed to start, or cannot initialize
CUDA, report that state clearly rather than treating validation as complete.

## Nomad Execution Checks

After pytest passes, validate the Nomad-facing path:

- Import the adapter from the same environment that will run Nomad.
- Start `nomad serve` with the intended `nomad.yml` or use
  `nomad code-mode-exec` for tensor-heavy and `WellFormat` payloads.
- Call the tool with a representative input and compare the response schema to
  the model card.
- Confirm the output is JSON-friendly or WellFormat/Tensor-compatible as
  appropriate.

Prefer small, deterministic representative inputs for routine checks. Use full
scientific examples when the user is actively validating model behavior.
