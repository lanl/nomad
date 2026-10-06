# MCP Data Types

Nomad provides several base types for transferring scientific data over [MCP's Data Layer](https://modelcontextprotocol.io/docs/latest/learn/architecture#data-layer), as detailed below.
Use of these types is not a requirement for serving model with Nomad.

:::{important}
The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD",
"SHOULD NOT", "RECOMMENDED",  "MAY", and "OPTIONAL" in this document are to be
interpreted as described in [RFC-2119][].
:::

## Tensors

Use {py:data}`nomad.well_format.Tensor` for an unconstrained tensor or
{py:func}`nomad.well_format.TensorField` to describe and validate a
floating-point tensor with a minimum rank. In addition three predefined
annotations are provided:

| Annotation | Shape convention | Field order |
| --- | --- | --- |
| {py:data}`~nomad.well_format.T0_Tensor` | `T ...` | Scalar (rank 0) |
| {py:data}`~nomad.well_format.T1_Tensor` | `T ... i` | Vector (rank 1) |
| {py:data}`~nomad.well_format.T2_Tensor` | `T ... i j` | Rank 2 |

Here, `T` is the time axis, `...` represents zero or more spatial axes, and
`i` and `j` are component axes. These annotations validate floating-point
dtype and minimum tensor rank. The axis labels document the expected shape but
do not enforce axis semantics or fixed dimension sizes.

{py:data}`nomad.well_format.Tensor` is a {py:class}`torch.Tensor`, with Pydantic annotations to add serialization and a JSON schema.
Serialization detaches and moves the tensor to the CPU before serializing with {py:func}`torch.save`, followed by zstd compression and base64 encoding.
Nomad explicitly tests that data, shape and {py:class}`~torch.dtype` are preserved, but other attributes are expected to survive as well.
This secialization scheme is ~22x faster than {py:meth}`torch.Tensor.tolist`, while remaining compatible with [MCP's data transport layer](https://modelcontextprotocol.io/docs/latest/learn/architecture#data-layer).


```{eval-rst}
.. autoclass:: nomad.well_format.Tensor

.. autoclass:: nomad.well_format.T0_Tensor

.. autoclass:: nomad.well_format.T1_Tensor

.. autoclass:: nomad.well_format.T2_Tensor

.. autofunction:: nomad.well_format.TensorField
```

## Well Format

Nomad's {py:class}`~nomad.well_format.WellFormat` is a structured schema for
exchanging gridded scientific state. It packages coordinates, boundary
conditions, scalar metadata, and rank-0, rank-1, and rank-2 tensor fields into
a single validated object that models and tools can share. It closely follows
the [Polymathic's The Well Data Format](https://polymathic-ai.org/the_well/data_format/)

This gives Nomad a common contract across three contexts: in-memory Python
objects, JSON or MCP payloads, and on-disk HDF5 files following the Well-style
layout. It is especially useful for rollout and surrogate PDE models, where a
tool needs to accept an initial physical state, evolve it over time, and return
a new state with the same semantics.

```{eval-rst}
.. autopydantic_model:: nomad.well_format.WellFormat

.. autopydantic_model:: nomad.well_format.BoundaryCondition

.. autopydantic_model:: nomad.well_format.Domain

```

## AutoRegressiveInput

Nomad's {py:class}`~nomad.well_format.AutoRegressiveInput` is a structured
`Input` for a {py:class}`~nomad.fm_base_tool.TorchModuleTool` for
autoregressive models that evolve a {py:class}`~nomad.well_format.WellFormat`
forward in time. This is the preferred input type for autoregressive PDE models
operating on regular grided data.

- A {py:class}`~nomad.fm_base_tool.TorchModuleTool` using this input schema
SHOULD have an output schema of {py:class}`~nomad.well_format.WellFormat`. If a
different output schema is used it SHALL conform to the following trajectory
requirements.
- The returned trajectory SHOULD NOT include the snapshots provided by
`initial_state`. The input `initial_state` MAY contain more than one input
snapshot.
- The returned trajectory SHOULD cover at least up to the requested `duration`
    - If duration is an integer, the returned trajectory SHALL contain
    `duration` snapshots
    - If duration is a float, the time stamp of the final snapshot SHOULD be
    greater than or equal to `duration`.
- The returned trajectory SHOULD NOT omit optional entries (i.e.,
boundary_conditions, dimensions).
- The returned trajectory SHALL NOT omit optional entries that do not exactly
match the entries provided by `initial_state`.


```{eval-rst}
.. autopydantic_model:: nomad.well_format.AutoRegressiveInput
```


[RFC-2119]: https://datatracker.ietf.org/doc/html/rfc2119
