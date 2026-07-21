# PDE And Field SciFM Adapters

Use this reference when connecting PDE, rollout, neural-operator, U-Net, FNO,
operator-transformer, simulator-surrogate, or other gridded field SciFMs to
Nomad.

## Contents

- Choose The Contract
- Metadata To Inspect
- WellFormat Pattern
- Adapter Shape
- Production Checks
- Testing And User Review

## Choose The Contract

Pick the smallest truthful contract supported by the model metadata:

- **Autoregressive rollout**: use `AutoRegressiveInput` when the model consumes
  an initial state and advances it by integer steps or duration.
- **Single operator prediction**: use a custom Pydantic input with a
  `WellFormat` state plus explicit controls when the model predicts one target
  state, one residual, or one time-conditioned field.
- **Field embedding/classification**: use `WellFormat` or `Tensor` input and a
  compact Pydantic output when the result is not a full field.

Do not assume all PDE SciFMs are rollouts. Some public checkpoints expose a
time-conditioned operator, a residual/delta predictor, a field-completion model,
or a dataset-specific fine-tuned head.

## Metadata To Inspect

Before writing `preprocess`, inspect the model card, config files, and upstream
code for:

- Field/channel names and order.
- Number of spatial dimensions and valid grid sizes.
- Whether tensors are channel-first, channel-last, time-major, or batch-major.
- Whether the model predicts absolute states, deltas/residuals, logits, or
  normalized values.
- Required normalization statistics and how to invert them.
- Boundary-condition encoding and whether the model expects masks, enum labels,
  periodicity flags, or auxiliary grids.
- Time semantics: one step, physical `dt`, continuous time conditioning, or
  dataset-relative index.

If metadata is incomplete, make the adapter fail clearly with the missing
fields rather than guessing a scientific convention.

After inspecting the code, ask the user to confirm the scientific contract:
which fields are inputs, which fields are predictions, units for each field,
allowed grids, whether values are normalized, and whether the model predicts an
absolute state, a delta, a residual, logits, or a derived diagnostic.

## WellFormat Pattern

Map scientific field names explicitly. Do not rely on dictionary order.

```python
CHANNELS = ("density", "velocity_x", "velocity_y", "pressure")


def state_to_channels(state: WellFormat) -> torch.Tensor:
    channels = []
    for name in CHANNELS:
        value = state.t0_fields[name]
        if value.ndim == state.n_spatial_dims + 1:
            value = value[0]
        channels.append(value.to(torch.float32))
    return torch.stack(channels, dim=0)
```

Common layouts:

- `(batch, channels, height, width)`
- `(batch, time, channels, height, width)`
- `(batch, channels, depth, height, width)`
- model-specific forms carrying boundary conditions, state labels, coordinates,
  or metadata objects alongside the tensor.

## Adapter Shape

Keep model-specific loading and metadata handling behind small helper functions:

```python
class FieldModelTool(
    TorchModuleTool[InputSchema, OutputSchema, dict[str, object], dict[str, object]]
):
    args_schema: type[InputSchema] = InputSchema
    output_schema: type[OutputSchema] = OutputSchema

    @classmethod
    def from_pretrained(cls, name_or_path: str, **kwargs):
        device = default_device()
        model, metadata = load_model_and_metadata(name_or_path, device=device)
        return cls(
            fm=model,
            name="field-model",
            description=description_from_metadata(metadata),
            batch_size=1,
            device=device,
            metadata=metadata,
        )

    def preprocess(self, inputs):
        return build_model_batch(inputs, self.metadata, self.device)

    def _forward(self, model_inputs):
        return call_model(self.fm, model_inputs)

    def postprocess(self, model_output):
        yield from convert_outputs_to_schema(model_output, self.metadata)
```

`from_pretrained` should work with the resolved model directory that Nomad
passes in. Avoid requiring arbitrary `nomad.yml` fields to reach the loader.

## Production Checks

- Confirm channel names, units, scaling, valid grid sizes, and boundary
  conventions from the training repo or dataset.
- Preserve coordinates and boundary conditions only when they remain valid for
  the predicted state.
- Keep `batch_size: 1` until GPU memory and response size are measured.
- Add upstream model packages and heavy dependencies to the adapter package,
  not to Nomad itself.

## Testing And User Review

- Put schema, preprocessing, and postprocessing tests in pytest. Use test-only
  fixtures for synthetic fields; do not add fake-model switches to production
  adapter code.
- Mark real-model tests that require CUDA with `@pytest.mark.gpu` and submit
  them to an appropriate GPU runtime or scheduler.
- Before asking the user to review, run a representative input through the real
  adapter path and check shapes, finite values, channel names, and units.
- Create review artifacts the user can understand: input frame plots, predicted
  frame plots, input-vs-output deltas, per-channel min/max/mean summaries,
  coordinates, and time-step notes.
- Ask the user whether the prediction is scientifically plausible and whether
  preprocessing, normalization, or output interpretation needs correction.
