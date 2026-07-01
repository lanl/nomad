# Model Hub Reference

Nomad can download and cache model weights from various sources. Config files
use the `name_or_path` field to choose the source.

| Name | Example |
| --- | --- |
| Local directory URI | `file:models/my-model` |
| Hugging Face URI | `hf://my-org/my-model` |
| ORAS artifact | `oras://registry.example.org/my-org/my-model:v1` |
| Git/Git LFS HTTPS URI | `git+https://example.org/my-org/models.git@main` |
| Git/Git LFS SSH URI | `git+ssh://git@example.org/my-org/models.git@main` |
| Local path[^path-or-hf] | `models/my-model` |
| Hugging Face repo ID[^path-or-hf] | `my-org/my-model` |
| Plain HTTPS Git URL[^compat-uri] | `https://example.org/my-org/models.git@main` |
| SCP-style Git URL[^compat-uri] | `git@example.org:my-org/models.git@main` |

Nomad resolves mutable remote sources, downloads them into a local cache, and
reuses cached artifacts across runs. Add `#path/to/dir` to any source when the
model files live in a subdirectory.

[^compat-uri]: Accepted for compatibility and resolved to URI forms. Plain
    `http://` Git URLs are handled the same way as `https://`.

[^path-or-hf]: Checked as local paths first. If no local path exists, Nomad
    treats them as Hugging Face repo IDs.

The Python API for these sources is {py:class}`nomad.hub.RepoSpec`. Use
`RepoSpec.resolved().uri()` or `RepoSpec.lock(...)` when application code needs
a pinned URI for a remote source.

```{eval-rst}
.. automodule:: nomad.hub
```
