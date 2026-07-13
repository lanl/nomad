from __future__ import annotations

from typing import Annotated, Literal

from openbabel import openbabel
from pydantic import AfterValidator, BaseModel


def validate_and_canonicalize_smiles(
    smi: str, encoding: Literal["smiles", "smiles-kekule"] = "smiles"
):
    mol = openbabel.OBMol()
    conv = openbabel.OBConversion()
    conv.SetInFormat("smi")

    if not conv.ReadString(mol, smi) or mol.NumAtoms() == 0:
        raise ValueError(f"Invalid SMILES string: {smi}")

    format = "c" if encoding == "smiles" else "k"
    conv.SetOutFormat("smi")
    conv.AddOption(format, conv.OUTOPTIONS)
    canonical_smiles = conv.WriteString(mol).strip()

    if not canonical_smiles:
        raise ValueError(f"Failed to generate canonical SMILES from: {smi}")

    return canonical_smiles


SMILES = Annotated[str, AfterValidator(validate_and_canonicalize_smiles)]


class Property(BaseModel):
    value: float | int
    units: str | None
    description: str | None


class MolecularProperties(BaseModel):
    molecule: SMILES
    properties: dict[str, Property]
