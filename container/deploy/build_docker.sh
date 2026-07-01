#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
source "${SCRIPT_DIR}/../build_common.sh"

DOCKER="${DOCKER:-docker}"

require_commands git uv "${DOCKER}"

if ! "${DOCKER}" info >/dev/null 2>&1; then
    echo "Docker is not available. Is the Docker daemon running?" >&2
    exit 1
fi

echo "Docker: $("${DOCKER}" --version)"
git --version
uv --version

prepare_deploy_context

build_args=()
create_git_credentials_build_args
build_args+=(--build-arg "NOMAD_BASE_IMAGE=${BASE_IMAGE}")
configure_docker_buildx_platform

echo "Building image ${IMAGE}" >&2
if env_truthy "${NOMAD_PUSH:-0}"; then
    build_args+=(--push)
elif ! env_has_multiple_platforms; then
    build_args+=(--load)
else
    echo "Skipping local image load for multi-platform Docker build." >&2
fi

DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}" "${DOCKER}" buildx build \
    "${build_args[@]}" \
    "$@" \
    -t "${IMAGE}" \
    -f "${DEPLOY_DOCKERFILE}" \
    "${DEPLOY_CONTEXT_DIR}"
