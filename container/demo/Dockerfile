# syntax=docker/dockerfile:1
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_CERTS=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_TORCH_BACKEND=cu130 \
    NOMAD_CACHE=/var/cache/nomad \
    HF_HOME=/var/cache/nomad/huggingface \
    HF_HUB_DISABLE_UPDATE_CHECK=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /nomad

# Rootless Buildah on Darwin cannot switch to apt's sandbox user.
RUN apt-get -o APT::Sandbox::User=root update \
 && apt-get -o APT::Sandbox::User=root install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      git-lfs \
      openbabel \
      python3-openbabel \
 && python --version \
 && git --version \
 && git-lfs --version \
 && git lfs install --system \
 && mkdir -p /run/secrets \
 && mkdir -p /etc/pip \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p "$NOMAD_CACHE" "$MPLCONFIGDIR" "$HF_HOME" \
 && chmod -R 777 /nomad "$NOMAD_CACHE" "$MPLCONFIGDIR" "$HF_HOME"

# Add runtime secret defaults
ADD ./container/git-credentials-k8-secrets.sh /usr/local/bin/git-credential-k8s-secret
RUN chmod 755 /usr/local/bin/git-credential-k8s-secret \
    && git config --system credential.helper /usr/local/bin/git-credential-k8s-secret

COPY pyproject.toml README.md ./
COPY src ./src
COPY container/demo ./container/demo

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --directory ./container/demo --locked --no-editable \
    && ln -sf $(realpath ./container/demo/.venv/bin/nomad) /usr/local/bin/

EXPOSE 38217
ENTRYPOINT ["nomad"]
CMD ["serve", "--transport=streamable-http", "--host=0.0.0.0", "--port=38217", "/nomad/container/demo/nomad.yml"]
