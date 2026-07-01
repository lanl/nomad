from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import torch
from pydantic import BaseModel, Field
from transformers import AutoModel, AutoTokenizer, DataCollatorWithPadding

from nomad.fm_base_tool import TorchModuleTool

from .smiles import (
    SMILES,
    MolecularProperties,
    Property,
    validate_and_canonicalize_smiles,
)


class Molecule(BaseModel):
    smi: SMILES = Field(description="SMILES string for one molecule")


class FinetunedMistModel(
    TorchModuleTool[
        Molecule,
        MolecularProperties,
        dict[str, torch.Tensor],
        dict[str, Any],
    ]
):
    """Small adapter for finetuned public MIST sequence models."""

    tokenizer: Any
    channels: list[str]
    channel_definitions: list[dict[str, Any]]
    encoding: str = "smiles"
    args_schema: type[Molecule] = Molecule
    output_schema: type[MolecularProperties] = MolecularProperties

    @classmethod
    def from_pretrained(cls, name_or_path: str, *args, **kwargs):
        batch_size = int(kwargs.pop("batch_size", 16))
        channels = kwargs.pop("channels", None)
        kwargs["trust_remote_code"] = kwargs.get("trust_remote_code", True)
        model = AutoModel.from_pretrained(name_or_path, *args, **kwargs)
        tokenizer = AutoTokenizer.from_pretrained(name_or_path, **kwargs)
        path = Path(name_or_path)
        name = path.name.replace(".", "p") if path.exists() else str(name_or_path)
        encoding = getattr(model.config, "encoding", "smiles")
        channel_definitions = _channel_definitions(model)
        selected_channels = channels or [item["name"] for item in channel_definitions]
        return cls(
            name=name,
            description="Predicts a molecular property for one SMILES string.",
            fm=model,
            tokenizer=tokenizer,
            channels=selected_channels,
            channel_definitions=channel_definitions,
            encoding=encoding,
            batch_size=batch_size,
        )

    def preprocess(self, inputs: Sequence[Molecule]) -> dict[str, Any]:
        smis = [item.smi for item in inputs]
        if self.encoding != "smiles":
            smis = [
                validate_and_canonicalize_smiles(smi, self.encoding) for smi in smis
            ]

        collate_fn = DataCollatorWithPadding(self.tokenizer)
        encoded = collate_fn(self.tokenizer(smis))
        return {
            "molecules": list(inputs),
            **{key: value.to(self.device) for key, value in encoded.items()},
        }

    def _forward(self, model_inputs: dict[str, Any]) -> dict[str, Any]:
        molecules = model_inputs.pop("molecules")
        outputs = self.fm(**model_inputs)
        logits = _as_tensor(outputs)
        return {"molecules": molecules, "output": logits.detach().cpu()}

    def postprocess(
        self, model_output: dict[str, Any]
    ) -> Iterable[MolecularProperties]:
        molecules = model_output["molecules"]
        values = model_output["output"].reshape(model_output["output"].shape[0], -1)
        for row, molecule in zip(values, molecules, strict=False):
            properties = {
                channel["name"]: Property(
                    value=float(value),
                    units=channel.get("units"),
                    description=channel.get("description"),
                )
                for channel, value in zip(
                    self.channel_definitions,
                    row.tolist(),
                    strict=False,
                )
                if channel["name"] in self.channels
            }
            yield MolecularProperties(molecule=molecule.smi, properties=properties)


def _channel_definitions(model: torch.nn.Module) -> list[dict[str, Any]]:
    channels = getattr(model, "channels", None) or getattr(
        getattr(model, "config", None),
        "channels",
        None,
    )
    if channels:
        return [
            channel if isinstance(channel, dict) else {"name": str(channel)}
            for channel in channels
        ]
    num_labels = getattr(getattr(model, "config", None), "num_labels", 1)
    names = (
        ["prediction"]
        if num_labels == 1
        else [f"prediction_{index}" for index in range(num_labels)]
    )
    return [{"name": name} for name in names]


def _as_tensor(output: Any) -> torch.Tensor:
    if isinstance(output, torch.Tensor):
        return output
    if hasattr(output, "logits"):
        return output.logits
    if isinstance(output, (tuple, list)):
        for value in output:
            if isinstance(value, torch.Tensor):
                return value
    raise TypeError(f"MIST model returned unsupported output type: {type(output)!r}")
