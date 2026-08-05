# Demo Server

This deployment is the public Nomad demo environment. It uses only code and
model artifacts that can be resolved from the public repository or public model
hubs.

For deployment, Compose, CA-bundle, and observability instructions, see the
[deployment guide](../../docs/deployments/guide.md).

Included tools:

- `nomad_demo.pubchem.search_pubchem`
- `nomad_demo.mist.FinetunedMistModel`
- `nomad_demo.diffunet2.DiffUnet2`
