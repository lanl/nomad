# Molecular SciFM Adapters

Use this reference when connecting molecular property, SMILES, SELFIES,
chemistry, graph, sequence, or mixture SciFMs to Nomad.

## Contents

- Choose The Contract
- Minimal Schemas
- Loading Pattern
- Adapter Notes
- Testing And User Review

## Choose The Contract

Prefer a small JSON-friendly interface. One call should usually represent one
molecule or one well-defined molecular query.

Common contracts:

- **Property prediction**: SMILES/SELFIES input, channel-named property output.
- **Embedding/featurization**: molecular string or graph input, vector output
  plus embedding metadata.
- **Generation**: prompt/scaffold input, generated molecules plus validity and
  filtering metadata.
- **Mixtures**: list of components plus fractions/conditions, structured
  property output.

Do not expose raw tensors unless a downstream agent genuinely needs them.

After inspecting the model package, ask the user to confirm the accepted input
representation and output meaning: SMILES, SELFIES, graph, conformer, mixture,
property names, units, whether outputs are regression values, logits,
probabilities, embeddings, or generated candidates, and what canonicalization
the model expects.

## Minimal Schemas

```python
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def validate_molecule_string(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Molecule string must be non-empty")
    return value


MoleculeString = Annotated[str, AfterValidator(validate_molecule_string)]


class MoleculeInput(BaseModel):
    molecule: MoleculeString = Field(
        description="Molecular representation accepted by the model, such as SMILES."
    )


class PredictedProperty(BaseModel):
    value: float | int | str | bool | list[float] | list[int] | list[str]
    units: str | None = None
    description: str | None = None


class MolecularProperties(BaseModel):
    molecule: MoleculeString
    properties: dict[str, PredictedProperty]
```

Validate and canonicalize with a real chemistry toolkit when scientific
correctness depends on it. Keep the validator light if the model has its own
canonicalizer or accepts non-SMILES formats.

## Loading Pattern

Inspect the model card and package API before choosing the inference path:

- Prefer an upstream `predict(...)`, `encode(...)`, or `generate(...)` method
  when it exists and documents preprocessing.
- Fall back to tokenizer/collator/model-forward only when the card or source
  confirms the expected inputs.
- Read channel metadata from model attributes or config.
- If channel metadata is missing, emit stable fallback names like
  `prediction`, `prediction_0`, or `embedding`.
- Prefer `units` in public response schemas.

For batch-oriented transformer models, keep batching inside
`TorchModuleTool.preprocess`; the public MCP call can stay scalar.

## Adapter Notes

- Store tokenizer/canonicalizer objects as Pydantic fields on the tool.
- Move encoded tensors to `self.device` in `preprocess`.
- Convert outputs to JSON-friendly scalars, lists, and Pydantic models in
  `postprocess`.
- Document whether returned values are regression values, logits, class labels,
  probabilities, embeddings, or generated candidates.
- Add model-specific dependencies such as `transformers`, `smirk`, RDKit, or
  Open Babel to the adapter package.

## Testing And User Review

- Use pytest for validators, canonicalization behavior, package imports,
  preprocessing, and postprocessing. Keep test doubles inside tests only; do
  not expose fake backends or mock switches through the production adapter.
- Mark real-model tests that need CUDA or large weights with
  `@pytest.mark.gpu` and run them in an appropriate GPU environment.
- Run representative molecules or molecular queries through the real adapter
  path before user review.
- Show the user the input representation, canonicalized form if any, predicted
  properties or candidates, units, score meanings, and validity/filtering
  notes.
- Ask the user whether the interface reflects how the molecular SciFM should be
  invoked and whether the predictions look scientifically plausible.
