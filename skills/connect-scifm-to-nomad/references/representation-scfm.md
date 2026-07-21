# Representation And Sequence SciFM Adapters

Use this reference for SciFMs whose primary input is a domain string or token
sequence and whose output is an embedding, masked-token prediction, score,
generated sequence, or other representation-level result. This includes many
genomics, crystal/materials, chemistry, protein, and language-like scientific
models.

## Contents

- Choose The Contract
- Loading Pattern
- Adapter Guidance
- Production Checks
- Testing And User Review

## Choose The Contract

Start from the model's native representation:

- DNA/RNA/protein: sequence string plus optional generation/scoring controls.
- Crystals/materials: SCOPE, formula, CIF-derived string, structure token
  sequence, or explicit structured input if the model requires coordinates.
- Masked-token models: input string with mask tokens plus `top_k` controls.
- Embedding models: input string/structure plus embedding vector and metadata.
- Causal generators: prompt plus generation settings and generated sequence.

If the representation model is generative, keep decoding bounded and explicit:
include maximum generated tokens/candidates, temperature or sampling controls,
and a clear safety/validity note for the scientific domain. For routine
validation, prefer scoring or embedding over open-ended generation unless
generation is the core requested workflow.

Keep outputs explicit:

- Include `token_count`, `truncated`, `model_name`, and `encoding` when helpful.
- For embeddings, include `embedding_size` and enough metadata to interpret the
  vector.
- Prefer `list[float]` or `list[list[float]]` for embedding outputs instead of
  `nomad.well_format.Tensor`, unless the embedding is too large for a
  JSON-friendly response or a downstream tensor workflow explicitly requires
  Nomad tensor encoding.
- For masked-token predictions, return positions, tokens, and scores.
- For generation, return the prompt, generated content, and decoding settings.

## Loading Pattern

Inspect the model card/source for whether to use:

- A domain package loader, such as `PackageModel.from_pretrained(...)`.
- Hugging Face `AutoModel`, `AutoModelForMaskedLM`,
  `AutoModelForCausalLM`, or `AutoTokenizer`.
- A local converter that turns domain files into model strings.

Do not assume that a pretrained representation model is a calibrated property
predictor. If property prediction requires a fine-tuned head, expose
embeddings or masked-token predictions unless the head is present.

## Adapter Guidance

Use the Model Builder guide
(`https://lanl.github.io/nomad/guides/model-builder.html`) for the generic
`TorchModuleTool` structure, and use the `TorchModuleTool` API reference
(`https://lanl.github.io/nomad/reference/api-torch-module-tool.html`) for method
semantics. For representation models, keep tokenization, truncation, generation
settings, and decode logic in small helpers that can be tested independently
from the server.

## Production Checks

- Confirm tokenizer vocabulary, special tokens, mask token, max length, and
  truncation behavior.
- Confirm that the model does not require multiple GPUs. `TorchModuleTool` is
  for single-GPU model execution; CPU support is useful as a fallback only when
  the model and dependencies already support it. If the model requires
  model-parallel or multi-GPU placement, it needs a separate wrapper/service
  that hides that placement behind a single tool interface.
- Avoid loading large weights during import; load only in `from_pretrained`.
- Keep generated outputs bounded with explicit max-token/max-candidate limits.
- Confirm with the user what the scientific input representation should be and
  what each output means: embedding, logits, probabilities, scores, generated
  candidates, or domain-specific labels.
- Include units where outputs are physical properties; explicitly say when
  outputs are unitless, normalized, logits, probabilities, or embeddings.

## Testing And User Review

- Put package-shape, schema, tokenization, truncation, and decode checks in
  pytest. Use test doubles only in test files or fixtures; production adapter
  code should load the real configured model or fail with a clear setup error.
- Mark real-model GPU checks with `@pytest.mark.gpu`.
- For user review, show representative inputs, tokenization/truncation notes,
  output size or top candidates, scores, and validity checks.
- Ask the user whether the accepted representation and output interpretation
  match how the SciFM is intended to be invoked.
