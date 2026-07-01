# Demo Deployment Guide

This page documents how to serve models with Nomad.
For rules and constraints, read {doc}`Deployment Specification <deployment-spec>`.

## Running Nomad in a Container

Public pre-built deployment images are not currently published.
Build a deployment image locally, or push one to a registry you control, before
using the examples below. The examples use `nomad-demo:latest` as the image
name.
Depending on what the server is configured to serve, the [Git credentials mount](#passing-git-credentials-to-the-image) may be optional.
The [cache mount](#caching-model-weights) is always optional and can be used to persist downloaded model weights across container restarts.

::::{tab-set}

:::{tab-item} Docker
```shell
docker run --rm \
   --gpus all \
   --publish 38217:38217 \
   --volume "$PWD/cache:/var/cache/nomad" \
   --volume "$PWD/secrets/git-credentials:/run/secrets/git-credentials:ro" \
   nomad-demo:latest \
   serve \
      --transport=streamable-http \
      --host=0.0.0.0 \
      --port=38217 \
      /nomad/nomad.yml
```
:::

:::{tab-item} Charliecloud
```shell
module load charliecloud
export CH_IMAGE_USERNAME=your_username
export CH_IMAGE_PASSWORD=your_token_or_password
ch-image pull --auth registry.example.org/nomad-demo:latest
ch-run --cdi nomad_demo:latest \
   --bind=./cache:/var/cache/nomad \
   --bind=./secrets/git-credentials:/run/secrets/git-credentials \
   -- \
   serve \
      --transport=streamable-http \
      --host=$(hostname) \
      --port=38217 \
      /nomad/nomad.yml
```
:::


:::{tab-item} Singularity/Apptainer
```shell
module load singularity
export SINGULARITY_DOCKER_USERNAME=your_username
export SINGULARITY_DOCKER_PASSWORD=your_token_or_password
singularity exec \
   --nv \
   --bind=./cache:/var/cache/nomad \
   --bind=./secrets/git-credentials:/run/secrets/git-credentials:ro \
   docker://registry.example.org/nomad-demo:latest \
   nomad serve \
      --transport=streamable-http \
      --host=$(hostname) \
      --port=38217 \
      /nomad/nomad.yml
```
:::

::::

### Caching Model Weights

Nomad downloads model weights into `/var/cache/nomad`.
Mount this path as a persistent volume to reuse downloaded artifacts across container restarts.
The cache can also be shared by multiple container instances when the backing filesystem supports the required locking behavior.
See {doc}`Model Hub Reference </reference/api-hub>` for details on how Nomad resolves and caches local, Hugging Face, Git-backed, and ORAS-backed model artifacts.


:::{warning}
Sharing a writable cache across multiple instances requires the backing filesystem to support reliable file locking.
For network or HPC filesystems, validate locking behavior first, or pre-populate the cache and mount it read-only.
:::

## Passing Secrets to the Container

The Nomad containers read secrets, such as access tokens and CA certificates, from the `/run/secrets` directory.

### Passing Git Credentials to the Image

Git credentials are managed by a [Git credential helper](https://git-scm.com/book/en/v2/Git-Tools-Credential-Storage) installed into the container.
The {repo_file}`helper <container/git-credentials-k8-secrets.sh>` reads credentials passed to the container in one of the following formats:

1. As a [git-credential-store](https://git-scm.com/docs/git-credential-store) at `/run/secrets/git-credentials`
2. As a directory of credential files at `/run/secrets/git-credentials/<host>/`
3. Via environment variables `GIT_CREDENTIAL_*`

Passing credentials as a single file versus directory structure is mutually exclusive.
Environment variables are always supported as a fallback.

::::{tab-set}

:::{tab-item} Credential Store Directory

Mount a directory at `/run/secrets/git-credentials`.
For each Git host, create a subdirectory that follows Kubernetes' [Basic Authentication Secret](https://kubernetes.io/docs/concepts/configuration/secret/#basic-authentication-secret) keys:

```text
/run/secrets/git-credentials/<host>/username
/run/secrets/git-credentials/<host>/password
```

For Kubernetes, create a Basic Authentication Secret per host, then mount or project those Secret keys under the matching host directory.
The Kubernetes documentation shows how to [expose Secret data through a volume](https://kubernetes.io/docs/tasks/inject-data-application/distribute-credentials-secure/#create-a-pod-that-has-access-to-the-secret-data-through-a-volume).

```shell
kubectl create secret generic github-credentials \
  --type=kubernetes.io/basic-auth \
  --from-literal=username=oauth2 \
  --from-literal=password=ACCESS_TOKEN
```

:::

:::{tab-item} Credential Store File

Mount a single [git-credential-store](https://git-scm.com/docs/git-credential-store) file at `/run/secrets/git-credentials`.
The file contains one credential URL per line:

```text
https://oauth2:ACCESS_TOKEN@github.com
https://oauth2:ACCESS_TOKEN@gitlab.com
```

Replace `ACCESS_TOKEN` with your access token.
If the username or token contains URL-special characters, percent-encode them before writing the credential URL.

:::

:::{tab-item} Env Variables

Credentials can be passed as environment variables using the following format:

| Env Variable | Content |
|---|---|
| `GIT_CREDENTIAL_<HOST>_USERNAME` | Username for `HOST` |
| `GIT_CREDENTIAL_<HOST>_PASSWORD` | Password or access token for `HOST` |

Where `<HOST>` is the hostname in all-caps after replacing `.`, `-`, and other separators with `_`.
For example, `github.com` becomes `GITHUB_COM`, and `git.example.com:8443` becomes `GIT_EXAMPLE_COM_8443`.

The following would configure credentials for github.com and gitlab.com:

```shell
export GIT_CREDENTIAL_GITHUB_COM_USERNAME=oauth2
export GIT_CREDENTIAL_GITHUB_COM_PASSWORD=GITHUB_ACCESS_TOKEN
export GIT_CREDENTIAL_GITLAB_COM_USERNAME=oauth2
export GIT_CREDENTIAL_GITLAB_COM_PASSWORD=GITLAB_ACCESS_TOKEN
```

:::

::::

### Passing ORAS Registry Credentials to the Image

Nomad reads ORAS registry credentials from a Docker-compatible auth file.
Create one with the registry client you normally use, then mount it into the
container and set `NOMAD_AUTH_FILE`. Nomad also accepts repository-scoped
`auths` entries and converts `username`/`password` entries to the host-level
basic auth form expected by ORAS.

For example, to create a Docker auth file:

```shell
mkdir -p secrets/docker
DOCKER_CONFIG="$PWD/secrets/docker" docker login registry.example.com
```

Then mount that file when the container needs to pull private `oras://` model sources:

```shell
docker run --rm \
   --env NOMAD_AUTH_FILE=/run/secrets/docker-config.json \
   --volume "$PWD/secrets/docker/config.json:/run/secrets/docker-config.json:ro" \
   registry.example.com/nomad-demo:latest
```

If `NOMAD_AUTH_FILE` is not set, Nomad falls back to `DOCKER_CONFIG/config.json`,
then `~/.docker/config.json`.
Use the same variable when running `nomad export --to oras` outside the image if the ORAS registry credentials are not in your default Docker config.

### Passing Custom CA Certificates to the Image

Nomad uses Python's system trust store integration for TLS certificates.
The image sets `SSL_CERT_FILE` to `/run/secrets/ca-certificates.crt` and includes a default bundle at that path.
Bind-mount a replacement bundle when the runtime environment needs additional certificate authorities.
Mount this file separately from Git credentials so the default bundle remains visible unless you intentionally replace it.

The mounted file must be a complete CA bundle, not just the additional corporate or proxy root certificate.

(building-the-image)=
## Building the Image

The base image defaults to `nomad:latest` and contains only Nomad, not deployment dependencies, config, or weights.
Deployment images default to `NOMAD_BASE_IMAGE=nomad:latest` and `NOMAD_DEPLOY=demo`.

| Setting | Purpose |
| --- | --- |
| `NOMAD_BASE_IMAGE` | Base image used for the deployment image. |
| `NOMAD_DEPLOY` | Deployment profile under `container/deploy/`; defaults to `demo`. |
| `NOMAD_INCLUDE_WEIGHTS=1` | Export configured model assets and include them in the deployment build context. |
| `NOMAD_EXPORT_TARGET` | Export destination for included weights; defaults to `disk`. Set to `oras` to push model weights to an ORAS registry and rewrite `nomad.yml` to `oras://` URIs. |
| `NOMAD_ORAS_REGISTRY` | Existing ORAS artifact repository used when `NOMAD_EXPORT_TARGET=oras`. |
| `NOMAD_AUTH_FILE` | Docker-compatible auth file for ORAS credentials when they are outside the default Docker config. |
| `NOMAD_PLATFORMS` | Platform or comma-separated platforms to build. |
| `NOMAD_PUSH=1` | Push the resulting image to a registry. |

::::{tab-set}

:::{tab-item} Docker
Run {repo_file}`./container/build_base_docker.sh` to build the base Nomad image, then run {repo_file}`./container/deploy/build_docker.sh` to build a deployment image with [Docker](https://docs.docker.com/build/).
By default, this builds the current platform and loads the image into the local Docker daemon.

The base image defaults to `nomad:latest` and contains only Nomad, not deployment dependencies, config, or weights.
Deployment images default to `NOMAD_BASE_IMAGE=nomad:latest` and `NOMAD_DEPLOY=demo`.
Set `NOMAD_INCLUDE_WEIGHTS=1` to export configured model assets with `nomad export --to disk` and copy them into the deployment image; the default is to include only `nomad.yml`. Set `NOMAD_EXPORT_TARGET` only when you intentionally want a different export mode.

Use `NOMAD_PLATFORMS` to choose the platform or platforms to build:

```shell
./container/build_base_docker.sh
NOMAD_PLATFORMS=linux/arm64 ./container/deploy/build_docker.sh
```

Use comma-separated platforms for a multi-arch image.
Multi-arch Docker builds require `NOMAD_PUSH=1`, because Docker cannot load a multi-platform manifest into the local daemon:

```shell
NOMAD_IMAGE=registry.example.com/nomad-demo:latest \
NOMAD_PLATFORMS=linux/amd64,linux/arm64 \
NOMAD_PUSH=1 \
./container/deploy/build_docker.sh
```

Set `NOMAD_PUSH=1` to push the image to a registry:

```shell
NOMAD_IMAGE=registry.example.com/nomad-demo:latest \
NOMAD_PUSH=1 \
./container/deploy/build_docker.sh
```
:::

:::{tab-item} Buildah
Run {repo_file}`./container/build_base_buildah.sh` to build the base Nomad image, then run {repo_file}`./container/deploy/build_buildah.sh` to build a deployment image with [Buildah](https://buildah.io/).
By default, this builds the current platform as a local image.

The base image defaults to `nomad:latest` and contains only Nomad, not deployment dependencies, config, or weights.
Deployment images default to `NOMAD_BASE_IMAGE=nomad:latest` and `NOMAD_DEPLOY=demo`.
Set `NOMAD_INCLUDE_WEIGHTS=1` to export configured model assets with `nomad export --to disk` and copy them into the deployment image; the default is to include only `nomad.yml`. Set `NOMAD_EXPORT_TARGET` only when you intentionally want a different export mode.

Use `NOMAD_PLATFORMS` to choose the platform or platforms to build:

```shell
./container/build_base_buildah.sh
NOMAD_PLATFORMS=linux/arm64 ./container/deploy/build_buildah.sh
```

Use comma-separated platforms for a multi-arch image:

```shell
NOMAD_IMAGE=registry.example.com/nomad-demo:latest \
NOMAD_PLATFORMS=linux/amd64,linux/arm64 \
./container/deploy/build_buildah.sh
```

Set `NOMAD_PUSH=1` to push the image to a registry:

```shell
NOMAD_IMAGE=registry.example.com/nomad-demo:latest \
NOMAD_PUSH=1 \
./container/deploy/build_buildah.sh
```
:::

::::
