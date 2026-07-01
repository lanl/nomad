from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import override

import torch

from nomad.fm_base_tool import TorchModuleTool, default_device
from nomad.well_format import AutoRegressiveInput, Domain, WellFormat

from .ddpm import DDPMPredictor


class DiffUnet2(TorchModuleTool):
    args_schema: type[AutoRegressiveInput] = AutoRegressiveInput
    output_schema: type[WellFormat] = WellFormat

    fm: DDPMPredictor

    resolution: tuple[int, int] = (560, 200)
    num_steps: int = 50
    eta: float = 1.0
    seed: int | None = None

    @classmethod
    def from_pretrained(cls, name_or_path, **kwargs):
        device = default_device()
        fm = DDPMPredictor.from_pretrained(name_or_path).eval().to(device)
        description = _description(name_or_path)
        return cls(
            fm=fm,
            name=Path(name_or_path).name,
            description=description,
            device=device,
            resolution=(560, 200),
        )

    @override
    def preprocess(self, inputs: list[AutoRegressiveInput]):
        initial_states = []
        for input in inputs:
            av_density = input.initial_state.t0_fields["av_density"]
            assert isinstance(av_density, torch.Tensor)
            if av_density.ndim == 3:
                av_density = av_density[0]
            elif av_density.ndim != 2:
                raise RuntimeError("Invalid input shape for av_density")

            initial_states.append(av_density.to(torch.float32))

        return {"initial_states": initial_states, "inputs": inputs}

    @override
    def _forward(self, model_inputs):
        final_states = self.fm.predict_batch(
            model_inputs["initial_states"],
            num_steps=self.num_steps,
            eta=self.eta,
            seed=self.seed,
        )
        trajectories = [
            torch.stack(
                [
                    initial_state.detach().cpu(),
                    final_state.detach().cpu(),
                ]
            )
            for initial_state, final_state in zip(
                model_inputs["initial_states"],
                final_states,
                strict=False,
            )
        ]
        return {"trajectories": trajectories, **model_inputs}

    @override
    def postprocess(self, model_output) -> Iterable[WellFormat]:
        inputs: list[AutoRegressiveInput] = model_output["inputs"]
        trajectories: list[torch.Tensor] = model_output["trajectories"]
        for traj, input in zip(trajectories, inputs, strict=False):
            ic = input.initial_state
            duration = input.duration
            yield WellFormat(
                dataset_name=ic.dataset_name,
                grid_type=ic.grid_type,
                n_spatial_dims=ic.n_spatial_dims,
                dimensions=Domain(
                    spatial_dims=ic.dimensions.spatial_dims,
                    time=[0.0, duration],
                ),
                t0_fields={"av_density": traj},
            )


def _description(name_or_path: str) -> str:
    path = Path(name_or_path).expanduser()
    description_path = path / "description.md"
    if description_path.is_file():
        return description_path.read_text(encoding="utf-8")
    return "Predicts the final HEAT PLI simulation frame using Janus-first2last."
