# TorchModuleTool Reference

`TorchModuleTool` is the base abstraction for serving PyTorch-backed model
inference through Nomad. It defines the preprocess, forward, and postprocess
pipeline that turns structured MCP inputs into model calls and model outputs
back into structured tool responses.

```{eval-rst}
.. autoclass:: nomad.fm_base_tool.TorchModuleTool
   :members:
   :private-members: _forward
   :undoc-members:
   :show-inheritance:
```
