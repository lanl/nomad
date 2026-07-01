# syntax=docker/dockerfile:1
ARG NOMAD_BASE_IMAGE=nomad:latest
FROM ${NOMAD_BASE_IMAGE}

COPY pyproject.toml uv.lock /tmp/nomad-deploy/
COPY src /tmp/nomad-deploy/src
RUN uv pip install \
        --directory /tmp/nomad-deploy \
        --system-certs \
        --system \
        --break-system-packages \
        --no-sources-package nomad \
        . \
        --requirements pyproject.toml \
        --constraints /etc/pip/constraint.txt \
        --override /etc/pip/override.txt \
    && rm -rf /tmp/nomad-deploy

COPY --chmod=0555 nomad.yml /nomad/nomad.yml
COPY --chmod=0555 models /nomad/models

EXPOSE 38217
ENTRYPOINT ["nomad"]
CMD ["serve", "--transport=streamable-http", "--port=38217", "/nomad/nomad.yml"]
