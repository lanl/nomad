# Code-Mode Gateway Reference

The code-mode gateway has two user-facing layers. The
{py:mod}`gateway server <nomad.gateway.server>` exposes search and execution
tools over MCP, while the {py:mod}`runtime client
<nomad.gateway.runtime.client>` installs the `mcp_tools.<server>` import hook
used inside sandboxed Python scripts. The {py:mod}`wrapper factory
<nomad.gateway.runtime.wrapper_factory>` is responsible for turning upstream MCP
schemas into importable sync and async Python callables.

## Runtime Behavior

Code-mode runs user Python in a child process with generated wrapper modules on
`PYTHONPATH`. The wrapper modules expose each upstream MCP tool as
`mcp_tools.<server>.<tool_name>` and `<tool_name>_async`.

There are two separate serialization paths to keep in mind:

- **Wrapper return deserialization** happens when sandboxed code calls an
  imported MCP tool wrapper.
- **Sandbox `RESULT` serialization** happens when the sandbox process exits and
  returns the value assigned to `RESULT` back to the gateway or
  [`nomad code-mode-exec`](cli-nomad-code-mode-exec).

### Wrapper Return Values

Generated wrappers normalize the upstream MCP response before returning it to
your sandboxed Python code:

- If the MCP response has `structuredContent`, wrappers return that value.
- If `structuredContent` is an object with only a `result` key, wrappers unwrap
  and return `structuredContent["result"]`.
- If `structuredContent` is absent and the response has MCP `content` blocks,
  wrappers normalize those content blocks into plain Python data where possible.
- Both sync and async wrappers apply the same normalization.

After normalization, wrappers apply schema-guided deserialization for Nomad
media fields. Today the supported Nomad media type is
`application/vnd.nomad.tensor`, which is encoded on the wire as a base64
zstd-compressed torch serialization. If the MCP `outputSchema` marks a field,
array item, `additionalProperties` value, or `anyOf`/`oneOf` branch with that
media type, the wrapper reconstructs it as a {py:class}`torch.Tensor` before
returning it.

This deserialization is recursive and resolves local JSON Schema `$ref` values.
It does **not** reconstruct arbitrary Pydantic models or domain objects around
the deserialized values. For example, a tool that returns a
{py:class}`~nomad.well_format.WellFormat`-shaped object may return a plain
`dict` whose tensor fields have already been decoded; call
`WellFormat.model_validate(result)` when you need the Pydantic object.

### Sandbox `RESULT` Values

{py:meth}`~nomad.gateway.server.CodeModeGateway.execute_mcp_code`,
{py:meth}`~nomad.gateway.server.CodeModeGateway.execute_mcp_script`, and
[`nomad code-mode-exec`](cli-nomad-code-mode-exec) return the final value
assigned to the global name `RESULT`. If `RESULT` is never assigned, the
returned `result` is `null`. If it is assigned multiple times, only the final
value is kept.

The sandbox writes `RESULT` through JSON. The fallback serializer preserves
common values as follows:

| Python value | Returned JSON value |
| --- | --- |
| {py:data}`None`, {py:class}`str`, {py:class}`int`, {py:class}`float`, {py:class}`bool` | unchanged |
| {py:class}`list`, {py:class}`set`, {py:class}`frozenset` | JSON array, with values serialized recursively |
| {py:class}`dict` | JSON object, with keys and values serialized recursively |
| {py:class}`Pydantic-like object <pydantic.BaseModel>` with {py:meth}`~pydantic.BaseModel.model_dump` | `model_dump(mode="json")`, falling back to recursive `model_dump(mode="python")` when needed |
| {py:class}`torch.Tensor` / {py:data}`nomad.well_format.Tensor` | Nomad tensor wire payload string |
| Other types | `str(value)` |

This means wrapper return values and final `RESULT` values do not have the same
type guarantees. A wrapper may return a {py:class}`torch.Tensor` inside the
sandbox, but `RESULT = tensor` serializes that tensor back to a JSON string
payload when the sandbox exits. For large or binary outputs, prefer writing
files in the workspace and returning a small JSON-friendly summary in `RESULT`.

### CLI Entry Point

[`nomad code-mode-exec`](cli-nomad-code-mode-exec) is the CLI entry point for
running one script through {py:meth}`~nomad.gateway.server.CodeModeGateway.run_script`
without starting a long-lived gateway. It returns a serialized
{py:class}`~nomad.gateway.sandbox.SandboxResult`; see
[`nomad code-mode-exec`](cli-nomad-code-mode-exec) for command syntax, output
handling, workspace selection, and script argument forwarding.

### Execution Environment

Sandboxed code runs with the generated wrapper package, the workspace directory,
standard-library paths, and installed package paths visible on `sys.path`. The
gateway uses the configured `workspace_root` as the child process working
directory. If that workspace contains `.venv/bin/python` or
`.venv/Scripts/python.exe`, the gateway uses that interpreter for sandbox
execution.

For persistent outputs, write files under the workspace directory. Temporary
implementation directories used by the gateway and generated wrappers are
cleaned up by the gateway process.

## Gateway Server

```{eval-rst}
.. automodule:: nomad.gateway.server
```

## Sandbox Result

```{eval-rst}
.. autoclass:: nomad.gateway.sandbox.SandboxResult
   :members:
```

## Runtime Client

```{eval-rst}
.. automodule:: nomad.gateway.runtime.client
```

## Wrapper Factory

```{eval-rst}
.. automodule:: nomad.gateway.runtime.wrapper_factory
```
