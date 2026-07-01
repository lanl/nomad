from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import numpy as np
import torch
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)
from pydantic.functional_validators import AfterValidator, BeforeValidator

from nomad.tensor_codac import BASE_TENSOR_SCHEMA, deserialize_tensor, serialize_tensor


def _to_tensor(v: Any) -> torch.Tensor:
    if isinstance(v, torch.Tensor):
        return v

    if isinstance(v, str):
        return _deserialize_tensor(v)

    return torch.as_tensor(v)


def _serialize_tensor(t: torch.Tensor):
    return serialize_tensor(t)


def _deserialize_tensor(tensor_b64: str):
    return deserialize_tensor(tensor_b64)


def _check_float_and_min_rank(min_rank: int):
    def _check(t: torch.Tensor) -> torch.Tensor:
        if not torch.is_floating_point(t):
            raise ValueError(f"Expected floating tensor, got dtype={t.dtype}")

        if t.ndim < min_rank:
            raise ValueError(
                f"Expected tensor with ndim >= {min_rank}, got shape={tuple(t.shape)}"
            )

        return t

    return _check


Tensor = Annotated[
    torch.Tensor,
    BeforeValidator(_to_tensor),
    PlainSerializer(_serialize_tensor),
    WithJsonSchema(BASE_TENSOR_SCHEMA),
]


def TensorField(*, min_rank: int, shape_str: str):
    """Return a Pydantic annotation for a floating tensor with minimum rank."""
    schema = {
        **BASE_TENSOR_SCHEMA,
        "description": f'Floating torch.Tensor with shape "{shape_str}" '
        f"encoded as a base64 zstd-compressed torch serialization",
    }

    return Annotated[
        torch.Tensor,
        BeforeValidator(_to_tensor),
        AfterValidator(_check_float_and_min_rank(min_rank)),
        PlainSerializer(_serialize_tensor),
        WithJsonSchema(schema),
    ]


T0_Tensor = TensorField(min_rank=1, shape_str="T ...")
T1_Tensor = TensorField(min_rank=2, shape_str="T ... i")
T2_Tensor = TensorField(min_rank=3, shape_str="T ... i j")


def _decode_h5_string(value: Any) -> str:
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode("utf-8")

    return str(value)


def _decode_h5_string_list(value: Any) -> list[str]:
    if value is None:
        return []

    raw_value = value.tolist() if hasattr(value, "tolist") else value
    if isinstance(raw_value, (str, bytes, np.bytes_)):
        raw_iter = [raw_value]
    else:
        raw_iter = raw_value

    return [_decode_h5_string(item) for item in raw_iter]


def _normalize_field_tensor(
    dataset: Any, *, n_spatial_dims: int, order: int
) -> torch.Tensor:
    tensor = torch.tensor(dataset[:])
    if "sample_varying" in dataset.attrs or "time_varying" in dataset.attrs:
        expected_ndim = (
            n_spatial_dims
            + order
            + int(bool(dataset.attrs.get("sample_varying", False)))
            + int(bool(dataset.attrs.get("time_varying", False)))
        )
        if tensor.ndim != expected_ndim:
            raise RuntimeError(
                f"Invalid shape {tensor.shape} for {dataset.name}; "
                f"expected ndim={expected_ndim} from sample_varying/time_varying attrs"
            )
        return tensor

    if tensor.ndim == (n_spatial_dims + order + 2):
        assert tensor.shape[0] == 1
        return tensor.squeeze(0)

    if tensor.ndim == (n_spatial_dims + order + 1):  # (Spatial..., order...)
        return tensor

    if tensor.ndim == (n_spatial_dims + order):  # (Spatial..., order...)
        return tensor.unsqueeze(0)

    raise RuntimeError(f"Invalid shape {tensor.shape} for {dataset.name}")


def _field_spatial_shape(
    tensor: torch.Tensor, *, n_spatial_dims: int, order: int
) -> tuple[int, ...]:
    min_ndim = n_spatial_dims + order
    max_ndim = min_ndim + 2
    if not min_ndim <= tensor.ndim <= max_ndim:
        raise ValueError(
            f"Expected tensor rank between {min_ndim} and {max_ndim} for "
            f"n_spatial_dims={n_spatial_dims} and tensor order={order}, "
            f"got shape={tuple(tensor.shape)}"
        )

    component_shape = tuple(tensor.shape[-order:]) if order else ()
    if component_shape != (n_spatial_dims,) * order:
        raise ValueError(
            f"Expected tensor component shape {(n_spatial_dims,) * order} for "
            f"n_spatial_dims={n_spatial_dims} and tensor order={order}, "
            f"got shape={tuple(tensor.shape)}"
        )

    start = tensor.ndim - order - n_spatial_dims
    stop = tensor.ndim - order if order else tensor.ndim
    return tuple(tensor.shape[start:stop])


class BoundaryCondition(BaseModel):
    """Boundary condition mask and optional values for one or more fields."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    associated_dims: list[str] = Field(default_factory=list)
    """Dimension names associated with this boundary condition."""

    associated_fields: list[str] = Field(default_factory=list)
    """Field names associated with this boundary condition."""

    bc_type: str
    """Boundary condition type label, such as Dirichlet or Neumann."""

    sample_varying: bool = False
    """Whether the boundary condition varies across samples."""

    time_varying: bool = False
    """Whether the boundary condition varies across time steps."""

    mask: Tensor
    """Boolean tensor selecting boundary locations."""

    values: Tensor | None = None
    """Optional tensor of values applied at masked boundary locations."""

    @model_validator(mode="after")
    def validate_mask(self):
        if self.mask.dtype != torch.bool:
            raise ValueError(
                f"Expected boundary condition mask to be boolean, got {self.mask.dtype}"
            )

        if self.values is None:
            return self

        if not torch.is_floating_point(self.values):
            raise ValueError(
                f"Expected boundary condition values to be floating, got "
                f"{self.values.dtype}"
            )

        selected_count = int(torch.count_nonzero(self.mask))
        if self.values.ndim == 0 or self.values.shape[0] != selected_count:
            raise ValueError(
                "Expected boundary condition values first dimension to match the "
                "number of masked entries; got "
                f"values_shape={tuple(self.values.shape)} and "
                f"selected_count={selected_count}"
            )

        return self


class Domain(BaseModel):
    """Coordinate metadata for spatial and temporal dimensions."""

    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, list[float]] = Field(init=False)

    spatial_dims: list[str] | None = None
    """Names of spatial coordinate dimensions."""

    time: list[float] | list[list[float]] | None = None
    """Time coordinates or per-sample time coordinates."""

    @model_validator(mode="after")
    def validate_spatial_dims(self):
        # Allow grid coordinate data
        if self.model_extra:
            coord_names = set(self.model_extra)
            spatial_dim_names = set(self.spatial_dims or [])
            if coord_names != spatial_dim_names:
                raise ValueError(
                    "Coordinate keys must exactly match spatial_dims; got "
                    f"coordinate keys={sorted(coord_names)} and "
                    f"spatial_dims={sorted(spatial_dim_names)}"
                )

        return self


class WellFormat(BaseModel):
    """Serializable container for gridded scientific fields and metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    dataset_name: str
    """Human-readable dataset name."""

    grid_type: str
    """Grid topology label for the dataset."""

    n_spatial_dims: int
    """Number of spatial dimensions represented by field tensors."""

    dimensions: Domain = Field(default_factory=Domain)
    """Spatial and temporal coordinate metadata."""

    boundary_conditions: dict[str, BoundaryCondition] = Field(default_factory=dict)
    """Boundary conditions keyed by boundary name."""

    scalars: dict[str, float | int] = Field(default_factory=dict)
    """Scalar metadata values keyed by name."""

    t0_fields: dict[str, T0_Tensor] = Field(default_factory=dict)
    """Scalar fields keyed by field name."""

    t1_fields: dict[str, T1_Tensor] = Field(default_factory=dict)
    """Vector fields keyed by field name."""

    t2_fields: dict[str, T2_Tensor] = Field(default_factory=dict)
    """Rank-2 tensor fields keyed by field name."""

    def __getitem__(self, item):
        return getattr(self, item)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, item, default=None):
        return getattr(self, item, default)

    @model_validator(mode="after")
    def validate_dimensions_and_field_shapes(self):
        spatial_dims = self.dimensions.spatial_dims
        if spatial_dims is not None and len(spatial_dims) != self.n_spatial_dims:
            raise ValueError(
                f"Expected {self.n_spatial_dims} spatial_dims, got {len(spatial_dims)}"
            )

        expected_spatial_shape: tuple[int, ...] | None = None
        if spatial_dims and self.dimensions.model_extra:
            expected_spatial_shape = tuple(
                len(self.dimensions.model_extra[dim_name]) for dim_name in spatial_dims
            )

        for order, tensor_order in enumerate(["t0_fields", "t1_fields", "t2_fields"]):
            fields = getattr(self, tensor_order)
            for field_name, tensor in fields.items():
                spatial_shape = _field_spatial_shape(
                    tensor, n_spatial_dims=self.n_spatial_dims, order=order
                )
                if (
                    expected_spatial_shape is not None
                    and spatial_shape != expected_spatial_shape
                ):
                    raise ValueError(
                        f"Spatial shape for {tensor_order}.{field_name} does not "
                        "match dimensions coordinates; got "
                        f"{spatial_shape}, expected {expected_spatial_shape}"
                    )

        return self

    @staticmethod
    def from_file(file: str | Path):
        """Load a Well HDF5 file into a :class:`WellFormat` instance."""
        file = Path(file)
        import h5py

        with h5py.File(file, "r") as h5:
            n_spatial_dims = int(h5.attrs["n_spatial_dims"])
            fields = {}
            for order, tensor_order in enumerate(
                ["t0_fields", "t1_fields", "t2_fields"]
            ):
                t_field = {}
                for k, v in h5.get(tensor_order, {}).items():
                    t_field[k] = _normalize_field_tensor(
                        v,
                        n_spatial_dims=n_spatial_dims,
                        order=order,
                    )
                if t_field:
                    fields[tensor_order] = t_field

            boundary_conditions = {}
            for name, boundary_group in h5.get("boundary_conditions", {}).items():
                boundary_conditions[name] = BoundaryCondition(
                    associated_dims=_decode_h5_string_list(
                        boundary_group.attrs.get("associated_dims")
                    ),
                    associated_fields=_decode_h5_string_list(
                        boundary_group.attrs.get("associated_fields")
                    ),
                    bc_type=_decode_h5_string(boundary_group.attrs["bc_type"]),
                    sample_varying=bool(
                        boundary_group.attrs.get("sample_varying", False)
                    ),
                    time_varying=bool(boundary_group.attrs.get("time_varying", False)),
                    mask=torch.tensor(boundary_group["mask"][:]),
                    values=(
                        torch.tensor(boundary_group["values"][:])
                        if "values" in boundary_group
                        else None
                    ),
                )

            dims_group = h5["dimensions"]
            raw_spatial_dims = dims_group.attrs.get("spatial_dims")
            spatial_dims: list[str] | None = None
            if raw_spatial_dims is not None:
                spatial_dims = _decode_h5_string_list(raw_spatial_dims)
                if not spatial_dims:
                    spatial_dims = []

            time = dims_group["time"][:].tolist() if "time" in dims_group else []
            extra_coords: dict[str, list[float]] = {}
            if spatial_dims:
                for dim_name in spatial_dims:
                    if dim_name in dims_group:
                        extra_coords[dim_name] = dims_group[dim_name][:].tolist()

            dimensions = Domain(spatial_dims=spatial_dims, time=time, **extra_coords)

            return WellFormat(
                dataset_name=h5.attrs.get("dataset_name") or file.name,
                n_spatial_dims=n_spatial_dims,
                grid_type=h5.attrs["grid_type"],
                dimensions=dimensions,
                boundary_conditions=boundary_conditions,
                scalars={
                    k: h5["scalars"][k][()].item() for k in h5.get("scalars", {}).keys()
                },
                **fields,
            )

    def to_file(self, file: str | Path):
        """Write this instance to a Well HDF5 file."""
        file = Path(file)
        file.parent.mkdir(parents=True, exist_ok=True)

        import h5py

        def _prepare_tensor(t: torch.Tensor, *, order: int):
            arr = torch.as_tensor(t, device="cpu")
            target_ndim = self.n_spatial_dims + order + 2
            while arr.ndim < target_ndim:
                arr = arr.unsqueeze(0)
            return arr.detach().cpu().numpy()

        with h5py.File(file, "w") as h5:
            h5.attrs["dataset_name"] = self.dataset_name
            h5.attrs["grid_type"] = self.grid_type
            h5.attrs["n_spatial_dims"] = self.n_spatial_dims

            if self.scalars:
                scalars_group = h5.create_group("scalars")
                for key, value in self.scalars.items():
                    scalars_group.create_dataset(
                        key,
                        data=np.array(value),
                    )

            dims_group = h5.create_group("dimensions")
            spatial_dims = self.dimensions.spatial_dims
            if spatial_dims is not None:
                dims_group.attrs["spatial_dims"] = np.array(spatial_dims, dtype=object)
            else:
                dims_group.attrs["spatial_dims"] = np.array([], dtype=object)

            time = self.dimensions.time or []
            dims_group.create_dataset("time", data=np.asarray(time, dtype=float))
            for dim_name, coords in getattr(self.dimensions, "model_extra", {}).items():
                dims_group.create_dataset(
                    dim_name,
                    data=np.asarray(coords, dtype=float),
                )

            if self.boundary_conditions:
                boundary_group = h5.create_group("boundary_conditions")
                for name, boundary in self.boundary_conditions.items():
                    group = boundary_group.create_group(name)
                    group.attrs["associated_dims"] = np.array(
                        boundary.associated_dims, dtype=object
                    )
                    group.attrs["associated_fields"] = np.array(
                        boundary.associated_fields, dtype=object
                    )
                    group.attrs["bc_type"] = boundary.bc_type
                    group.attrs["sample_varying"] = boundary.sample_varying
                    group.attrs["time_varying"] = boundary.time_varying
                    group.create_dataset(
                        "mask",
                        data=torch.as_tensor(
                            boundary.mask, dtype=torch.bool, device="cpu"
                        )
                        .detach()
                        .cpu()
                        .numpy(),
                    )
                    if boundary.values is not None:
                        group.create_dataset(
                            "values",
                            data=torch.as_tensor(boundary.values, device="cpu")
                            .detach()
                            .cpu()
                            .numpy(),
                        )

            for order, tensor_order in enumerate(
                ["t0_fields", "t1_fields", "t2_fields"]
            ):
                fields = getattr(self, tensor_order)
                if not fields:
                    continue

                group = h5.create_group(tensor_order)
                for key, tensor in fields.items():
                    group.create_dataset(key, data=_prepare_tensor(tensor, order=order))


class AutoRegressiveInput(BaseModel):
    """Input schema for models that roll out a Well state over time."""

    duration: int = Field(gt=0, description="Number of time steps to rollout")
    initial_state: WellFormat = Field(
        description="Initial WellFormat state of the simulation"
    )
