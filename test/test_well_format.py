from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
from pydantic import BaseModel, ConfigDict, ValidationError

from nomad.well_format import BoundaryCondition, Domain, Tensor, WellFormat

np = pytest.importorskip("numpy")


def test_well_format_roundtrip_preserves_tensors_and_metadata(tmp_path: Path):
    t0_tensor = torch.arange(24, dtype=torch.float64).reshape(2, 3, 4)
    t1_tensor = torch.arange(48, dtype=torch.float64).reshape(2, 3, 4, 2)
    t2_tensor = torch.arange(96, dtype=torch.float64).reshape(2, 3, 4, 2, 2)

    spatial_grids = {
        "x": [0.0, 0.5, 1.0],
        "y": [0.0, 1.0, 2.0, 3.0],
    }

    original = WellFormat(
        dataset_name="example",
        grid_type="structured",
        n_spatial_dims=2,
        dimensions=Domain(
            spatial_dims=["x", "y"],
            time=[0.0, 0.5, 1.0],
            **spatial_grids,
        ),
        boundary_conditions={
            "x_boundary": BoundaryCondition(
                associated_dims=["x"],
                associated_fields=["temperature"],
                bc_type="wall",
                mask=torch.tensor([True, False, True]),
                values=torch.tensor([5.0, 9.0], dtype=torch.float64),
            )
        },
        scalars={"pressure": 101.3, "well_count": 7},
        t0_fields={"temperature": t0_tensor},
        t1_fields={"velocity": t1_tensor},
        t2_fields={"stress": t2_tensor},
    )

    out_file = tmp_path / "roundtrip" / "state.h5"
    original.to_file(out_file)

    loaded = WellFormat.from_file(out_file)

    assert loaded.dataset_name == "example"
    assert loaded.grid_type == "structured"
    assert loaded.n_spatial_dims == 2
    assert loaded.dimensions.spatial_dims == ["x", "y"]
    assert loaded.dimensions.time == [0.0, 0.5, 1.0]
    assert loaded.dimensions.model_extra["x"] == pytest.approx(spatial_grids["x"])
    assert loaded.dimensions.model_extra["y"] == pytest.approx(spatial_grids["y"])
    assert loaded.boundary_conditions["x_boundary"].associated_dims == ["x"]
    assert loaded.boundary_conditions["x_boundary"].associated_fields == ["temperature"]
    assert loaded.boundary_conditions["x_boundary"].bc_type == "wall"
    assert torch.equal(
        loaded.boundary_conditions["x_boundary"].mask,
        torch.tensor([True, False, True]),
    )
    assert loaded.boundary_conditions["x_boundary"].mask.dtype == torch.bool
    assert torch.equal(
        loaded.boundary_conditions["x_boundary"].values,
        torch.tensor([5.0, 9.0], dtype=torch.float64),
    )
    assert loaded.boundary_conditions["x_boundary"].values.dtype == torch.float64
    assert loaded.scalars["pressure"] == pytest.approx(101.3)
    assert loaded.scalars["well_count"] == 7

    assert torch.equal(loaded.t0_fields["temperature"], t0_tensor)
    assert torch.equal(loaded.t1_fields["velocity"], t1_tensor)
    assert torch.equal(loaded.t2_fields["stress"], t2_tensor)

    assert loaded.t0_fields["temperature"].dtype == torch.float64
    assert loaded.t1_fields["velocity"].dtype == torch.float64
    assert loaded.t2_fields["stress"].dtype == torch.float64


def test_from_file_adjusts_tensor_shapes(tmp_path: Path):
    import h5py

    file_path = tmp_path / "manual.h5"
    with h5py.File(file_path, "w") as h5:
        h5.attrs["dataset_name"] = "manual"
        h5.attrs["grid_type"] = "structured"
        h5.attrs["n_spatial_dims"] = 2

        dims = h5.create_group("dimensions")
        dims.attrs["spatial_dims"] = np.array(["x", "y"], dtype=object)
        dims.create_dataset("time", data=np.asarray([0.0, 1.0], dtype=float))
        dims.create_dataset("x", data=np.asarray([0.0, 0.5, 1.0], dtype=float))
        dims.create_dataset("y", data=np.asarray([0.0, 1.0], dtype=float))

        boundary_conditions = h5.create_group("boundary_conditions")
        x_boundary = boundary_conditions.create_group("x_boundary")
        x_boundary.attrs["associated_dims"] = np.array(["x"], dtype=object)
        x_boundary.attrs["associated_fields"] = np.array(["pressure"], dtype=object)
        x_boundary.attrs["bc_type"] = "open"
        x_boundary.attrs["sample_varying"] = False
        x_boundary.attrs["time_varying"] = False
        x_boundary.create_dataset("mask", data=np.asarray([True, False, True]))
        x_boundary.create_dataset(
            "values", data=np.asarray([1.5, 2.5], dtype=np.float32)
        )

        scalars = h5.create_group("scalars")
        scalars.create_dataset("count", data=np.array(5))
        scalars.create_dataset("temperature", data=np.array(42.5))

        t0 = h5.create_group("t0_fields")
        t0.create_dataset("pressure", data=np.arange(6, dtype=np.float32).reshape(3, 2))

        t1 = h5.create_group("t1_fields")
        t1.create_dataset(
            "velocity",
            data=np.arange(24, dtype=np.float32).reshape(2, 3, 2, 2),
        )

    loaded = WellFormat.from_file(file_path)

    assert loaded.dataset_name == "manual"
    assert loaded.n_spatial_dims == 2
    assert loaded.dimensions.spatial_dims == ["x", "y"]
    assert loaded.dimensions.time == [0.0, 1.0]
    assert loaded.dimensions.model_extra["x"] == pytest.approx([0.0, 0.5, 1.0])
    assert loaded.dimensions.model_extra["y"] == pytest.approx([0.0, 1.0])
    assert loaded.boundary_conditions["x_boundary"].associated_dims == ["x"]
    assert loaded.boundary_conditions["x_boundary"].associated_fields == ["pressure"]
    assert loaded.boundary_conditions["x_boundary"].bc_type == "open"
    assert torch.equal(
        loaded.boundary_conditions["x_boundary"].mask,
        torch.tensor([True, False, True]),
    )
    assert torch.equal(
        loaded.boundary_conditions["x_boundary"].values,
        torch.tensor([1.5, 2.5], dtype=torch.float32),
    )
    assert loaded.scalars["count"] == 5
    assert loaded.scalars["temperature"] == pytest.approx(42.5)

    expected_t0 = torch.from_numpy(np.arange(6, dtype=np.float32).reshape(1, 3, 2))
    expected_t1 = torch.from_numpy(np.arange(24, dtype=np.float32).reshape(2, 3, 2, 2))

    assert torch.equal(loaded.t0_fields["pressure"], expected_t0)
    assert torch.equal(loaded.t1_fields["velocity"], expected_t1)


def test_from_file_preserves_sample_and_time_varying_batch_shape(tmp_path: Path):
    import h5py

    file_path = tmp_path / "spec_shape.h5"
    pressure = np.arange(24, dtype=np.float32).reshape(2, 3, 2, 2)

    with h5py.File(file_path, "w") as h5:
        h5.attrs["dataset_name"] = "spec-shape"
        h5.attrs["grid_type"] = "cartesian"
        h5.attrs["n_spatial_dims"] = 2
        h5.attrs["n_trajectories"] = 2

        dims = h5.create_group("dimensions")
        dims.attrs["spatial_dims"] = np.array(["x", "y"], dtype=object)
        dims.create_dataset("time", data=np.asarray([0.0, 0.5, 1.0], dtype=float))
        dims.create_dataset("x", data=np.asarray([0.0, 1.0], dtype=float))
        dims.create_dataset("y", data=np.asarray([0.0, 1.0], dtype=float))

        t0 = h5.create_group("t0_fields")
        pressure_ds = t0.create_dataset("pressure", data=pressure)
        pressure_ds.attrs["sample_varying"] = True
        pressure_ds.attrs["time_varying"] = True
        pressure_ds.attrs["dim_varying"] = np.array([True, True], dtype=bool)

    loaded = WellFormat.from_file(file_path)

    assert torch.equal(loaded.t0_fields["pressure"], torch.from_numpy(pressure))


def test_from_file_supports_sample_varying_time_dimension(tmp_path: Path):
    import h5py

    file_path = tmp_path / "sample_varying_time.h5"
    time = np.asarray([[0.0, 0.5, 1.0], [0.25, 0.75, 1.25]], dtype=np.float32)

    with h5py.File(file_path, "w") as h5:
        h5.attrs["dataset_name"] = "sample-varying-time"
        h5.attrs["grid_type"] = "cartesian"
        h5.attrs["n_spatial_dims"] = 2
        h5.attrs["n_trajectories"] = 2

        dims = h5.create_group("dimensions")
        dims.attrs["spatial_dims"] = np.array(["x", "y"], dtype=object)
        time_ds = dims.create_dataset("time", data=time)
        time_ds.attrs["sample_varying"] = True
        time_ds.attrs["time_varying"] = True
        dims.create_dataset("x", data=np.asarray([0.0, 1.0], dtype=float))
        dims.create_dataset("y", data=np.asarray([0.0, 1.0], dtype=float))

    loaded = WellFormat.from_file(file_path)

    assert np.allclose(np.asarray(loaded.dimensions.time), time)


def test_well_format_mapping_interface():
    wf = WellFormat(
        dataset_name="initial",
        grid_type="gridded",
        n_spatial_dims=0,
    )

    assert wf["dataset_name"] == "initial"
    wf["dataset_name"] = "updated"
    assert wf.dataset_name == "updated"
    assert wf.get("missing", "fallback") == "fallback"


def test_well_format_json_roundtrip():
    state = WellFormat(
        dataset_name="json-case",
        grid_type="structured",
        n_spatial_dims=2,
        dimensions=Domain(
            spatial_dims=["x", "y"],
            time=[0.0, 0.25, 0.5],
            x=[0.0, 0.5, 1.0],
            y=[0.0, 1.0, 2.0, 3.0],
        ),
        boundary_conditions={
            "x_boundary": BoundaryCondition(
                associated_dims=["x"],
                associated_fields=["temperature"],
                bc_type="periodic",
                mask=torch.tensor([True, False, True]),
                values=torch.tensor([1.0, 2.0], dtype=torch.float32),
            )
        },
        scalars={"pressure": 9.81, "count": 3},
        t0_fields={
            "temperature": torch.arange(12, dtype=torch.float64).reshape(3, 4),
            "salinity": torch.linspace(0.0, 1.0, 12, dtype=torch.float64).reshape(3, 4),
        },
        t1_fields={
            "velocity": torch.arange(48, dtype=torch.float64).reshape(2, 3, 4, 2)
        },
    )

    payload = state.model_dump_json()
    assert isinstance(payload, str)
    dumped = json.loads(payload)
    assert dumped["t0_fields"]["temperature"] != ""

    restored = WellFormat.model_validate_json(payload)

    assert restored.dataset_name == "json-case"
    assert restored.dimensions.time == [0.0, 0.25, 0.5]
    assert restored.scalars["pressure"] == pytest.approx(9.81)
    assert restored.scalars["count"] == 3
    assert restored.dimensions.model_extra["x"] == pytest.approx([0.0, 0.5, 1.0])
    assert restored.dimensions.model_extra["y"] == pytest.approx([0.0, 1.0, 2.0, 3.0])
    assert restored.boundary_conditions["x_boundary"].bc_type == "periodic"
    assert torch.equal(
        restored.boundary_conditions["x_boundary"].mask,
        torch.tensor([True, False, True]),
    )
    assert restored.boundary_conditions["x_boundary"].mask.dtype == torch.bool
    assert torch.equal(
        restored.boundary_conditions["x_boundary"].values,
        torch.tensor([1.0, 2.0], dtype=torch.float32),
    )
    assert restored.boundary_conditions["x_boundary"].values.dtype == torch.float32

    for key in ("temperature", "salinity"):
        restored_field = restored.t0_fields[key]
        assert restored_field.dtype == torch.float64
        assert torch.equal(restored_field, state.t0_fields[key])

    restored_velocity = restored.t1_fields["velocity"]
    assert restored_velocity.dtype == torch.float64
    assert torch.equal(restored_velocity, state.t1_fields["velocity"])


def test_generic_tensor_json_roundtrip_accepts_non_float_tensor():
    class TensorEnvelope(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        payload: Tensor

    payload = TensorEnvelope(payload=torch.tensor([[1, 2], [3, 4]], dtype=torch.int16))

    restored = TensorEnvelope.model_validate_json(payload.model_dump_json())

    assert restored.payload.dtype == torch.int16
    assert torch.equal(restored.payload, payload.payload)


def test_domain_validation_invalid():
    # Unknown spatial dims
    with pytest.raises(ValidationError):
        Domain(
            spatial_dims=["x", "y"],
            time=[
                1.0,
            ],
            z=[0.2, 0.3],
        )

    # Not all grids are defined
    with pytest.raises(ValidationError):
        Domain(
            spatial_dims=["x", "y"],
            time=[
                1.0,
            ],
            x=[0.2, 0.3],
        )


def test_boundary_condition_requires_boolean_mask():
    with pytest.raises(ValidationError):
        BoundaryCondition(
            associated_dims=["x"],
            bc_type="wall",
            mask=torch.tensor([0, 1, 0], dtype=torch.int64),
        )


def test_boundary_condition_requires_floating_values():
    with pytest.raises(ValidationError):
        BoundaryCondition(
            associated_dims=["x"],
            bc_type="wall",
            mask=torch.tensor([True, False, True]),
            values=torch.tensor([1, 2], dtype=torch.int64),
        )


def test_boundary_condition_values_must_match_selected_count():
    BoundaryCondition(
        associated_dims=["x"],
        bc_type="wall",
        mask=torch.tensor([True, False, True]),
        values=torch.tensor([1.0, 2.0]),
    )
    with pytest.raises(ValidationError):
        BoundaryCondition(
            associated_dims=["x"],
            bc_type="wall",
            mask=torch.tensor([True, False, True]),
            values=torch.tensor([1.0, 2.0, 3.0, 4.0]),
        )


def test_well_format_rejects_field_rank_incompatible_with_spatial_dims():
    with pytest.raises(ValidationError):
        WellFormat(
            dataset_name="bad-rank",
            grid_type="structured",
            n_spatial_dims=2,
            t0_fields={"temperature": torch.tensor([1.0])},
        )

    with pytest.raises(ValidationError):
        WellFormat(
            dataset_name="bad-rank",
            grid_type="structured",
            n_spatial_dims=1,
            t0_fields={"temperature": torch.zeros(1, 2, 3, 4, dtype=torch.float32)},
        )


def test_well_format_rejects_spatial_dim_count_mismatch():
    with pytest.raises(ValidationError):
        WellFormat(
            dataset_name="bad-dims",
            grid_type="structured",
            n_spatial_dims=2,
            dimensions=Domain(
                spatial_dims=["x"],
                x=[0.0, 1.0],
            ),
            t0_fields={"temperature": torch.zeros(2, 2, dtype=torch.float32)},
        )


def test_well_format_rejects_coordinate_length_mismatch():
    with pytest.raises(ValidationError):
        WellFormat(
            dataset_name="bad-coords",
            grid_type="structured",
            n_spatial_dims=2,
            dimensions=Domain(
                spatial_dims=["x", "y"],
                x=[0.0, 1.0],
                y=[0.0, 1.0],
            ),
            t0_fields={"temperature": torch.zeros(2, 3, dtype=torch.float32)},
        )


def test_well_format_rejects_tensor_component_shape_mismatch():
    with pytest.raises(ValidationError):
        WellFormat(
            dataset_name="bad-vector",
            grid_type="structured",
            n_spatial_dims=2,
            t1_fields={"velocity": torch.zeros(2, 3, 3, dtype=torch.float32)},
        )

    with pytest.raises(ValidationError):
        WellFormat(
            dataset_name="bad-tensor",
            grid_type="structured",
            n_spatial_dims=2,
            t2_fields={"stress": torch.zeros(2, 3, 2, 3, dtype=torch.float32)},
        )
