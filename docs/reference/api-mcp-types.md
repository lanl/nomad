# MCP Data Types

Nomad provides several base types for transferring scientific data over [MCP's Data Layer](https://modelcontextprotocol.io/docs/latest/learn/architecture#data-layer), as detailed below.
Use of these types is not a requirement of serving model with Nomad.

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
Nomad explicitly tests that data, shape and `dtype` are preserved, but other attributes are expected to survive as well.

## Well Format Reference

Nomad's {py:class}`~nomad.well_format.WellFormat` is a structured schema for
exchanging gridded scientific state. It packages coordinates, boundary
conditions, scalar metadata, and rank-0, rank-1, and rank-2 tensor fields into
a single validated object that models and tools can share. It closely follows
the [Polymathic's The Well Data Format](https://polymathic-ai.org/the_well/data_format/)

This gives Nomad a common contract across three contexts: in-memory Python
objects, JSON or MCP payloads, and on-disk HDF5 files following the Well-style
layout. It is especially useful for rollout and surrogate models, where a tool
needs to accept an initial physical state, evolve it over time, and return a
new state with the same semantics.

```{eval-rst}
.. automodule:: nomad.well_format
```


[RFC-2119]: https://datatracker.ietf.org/doc/html/rfc2119
