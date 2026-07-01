# Well Format Reference

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

## Tensor Type Alias

```{eval-rst}
.. py:data:: nomad.well_format.Tensor

   Pydantic annotation for a ``torch.Tensor`` encoded in JSON as Nomad's
   ``application/vnd.nomad.tensor`` media type.
```

```{eval-rst}
.. automodule:: nomad.well_format
```
