from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import torch
from transformers import AutoModel


class DDPMPredictor(torch.nn.Module):
    """AutoModel-backed wrapper for the public Janus first-to-last DDPM."""

    def __init__(self, model: torch.nn.Module):
        super().__init__()
        self.model = model

    @classmethod
    def from_pretrained(cls, name_or_path: str, **kwargs):
        kwargs["trust_remote_code"] = kwargs.get("trust_remote_code", True)
        model = AutoModel.from_pretrained(name_or_path, **kwargs)
        return cls(model)

    @property
    def device(self) -> torch.device:
        if hasattr(self.model, "device"):
            return self.model.device
        return next(self.model.parameters()).device

    @property
    def config(self):
        return self.model.config

    def predict(
        self,
        density: torch.Tensor | np.ndarray,
        *,
        num_steps: int = 50,
        eta: float = 1.0,
        seed: int | None = None,
    ) -> torch.Tensor:
        prediction = self.model.predict(
            _to_numpy(density),
            num_steps=num_steps,
            eta=eta,
            seed=seed,
        )
        return torch.as_tensor(prediction, dtype=torch.float32)

    def predict_batch(
        self,
        densities: Sequence[torch.Tensor | np.ndarray],
        *,
        num_steps: int = 50,
        eta: float = 1.0,
        seed: int | None = None,
    ) -> list[torch.Tensor]:
        return [
            self.predict(
                density,
                num_steps=num_steps,
                eta=eta,
                seed=_seed_for_index(seed, index),
            )
            for index, density in enumerate(densities)
        ]


def _to_numpy(density: torch.Tensor | np.ndarray) -> np.ndarray:
    if isinstance(density, torch.Tensor):
        return density.detach().cpu().numpy().astype(np.float32, copy=False)
    return np.asarray(density, dtype=np.float32)


def _seed_for_index(seed: int | None, index: int) -> int | None:
    if seed is None:
        return None
    return seed + index
