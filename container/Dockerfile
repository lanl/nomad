# syntax=docker/dockerfile:1
FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_SYSTEM_CERTS=1 \
    UV_TORCH_BACKEND=cu130 \
    NOMAD_CACHE=/var/cache/nomad \
    HF_HOME=/var/cache/nomad/huggingface \
    HF_HUB_DISABLE_UPDATE_CHECK=1 \
    MPLCONFIGDIR=/tmp/matplotlib

RUN mkdir -p /nomad
WORKDIR /nomad

# Rootless Buildah on some HPC systems cannot switch to apt's sandbox user.
RUN apt-get -o APT::Sandbox::User=root update \
 && apt-get -o APT::Sandbox::User=root install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      git-lfs \
 && python --version \
 && git --version \
 && git-lfs --version \
 && git lfs install --system \
 && mkdir -p /run/secrets \
 && mkdir -p /etc/pip \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.11.21 \
 && mkdir -p "$NOMAD_CACHE" "$MPLCONFIGDIR" "$HF_HOME" \
 && chmod -R 777 /nomad "$NOMAD_CACHE" "$MPLCONFIGDIR" "$HF_HOME"

# Add runtime secret defaults
COPY git-credentials-k8-secrets.sh /usr/local/bin/git-credential-k8s-secret
RUN chmod 755 /usr/local/bin/git-credential-k8s-secret \
    && git config --system credential.helper /usr/local/bin/git-credential-k8s-secret

COPY packages/nomad-*.whl ./packages/
RUN uv pip install \
    --system-certs \
    --system \
    --break-system-packages \
    packages/nomad-*.whl \
    && pip list --format=freeze --not-required > /etc/pip/constraint.txt \
    && NOMAD_WHEEL="$(readlink -f packages/nomad-*.whl)" \
    && printf 'nomad @ file://%s\n' "$NOMAD_WHEEL" > /etc/pip/override.txt

ENTRYPOINT ["nomad"]
