#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
source "${SCRIPT_DIR}/build_common.sh"

DOCKER="${DOCKER:-docker}"
IMAGE="${NOMAD_IMAGE:-}"
if [ -z "${IMAGE}" ] && [ -n "${NOMAD_IMAGE_TAGS:-}" ]; then
    IMAGE="$(first_line <<<"${NOMAD_IMAGE_TAGS}")"
fi
IMAGE="${IMAGE:-${BASE_IMAGE_TAG}}"

require_commands git "${DOCKER}"
if ! env_truthy "${NOMAD_PREPARED_CONTEXT:-0}"; then
    require_commands uv
fi

if ! "${DOCKER}" info >/dev/null 2>&1; then
    echo "Docker is not available. Is the Docker daemon running?" >&2
    exit 1
fi

echo "Docker: $("${DOCKER}" --version)"
git --version
if ! env_truthy "${NOMAD_PREPARED_CONTEXT:-0}"; then
    uv --version
fi

prepare_or_print_base_context

build_args=()
configure_docker_buildx_platform
add_image_metadata_build_args
if env_truthy "${NOMAD_PROVENANCE:-0}"; then
    build_args+=(--provenance "${NOMAD_PROVENANCE_MODE:-mode=max}")
fi

echo "Building base image ${IMAGE}" >&2
if env_truthy "${NOMAD_PUSH:-0}"; then
    build_args+=(--push)
elif [[ "${NOMAD_PLATFORMS:-}" != *,* ]]; then
    build_args+=(--load)
fi

DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}" "${DOCKER}" buildx build \
    "${build_args[@]}" \
    "$@" \
    -t "${IMAGE}" \
    -f "${BASE_DOCKERFILE}" \
    "${BASE_CONTEXT_DIR}"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
    printf 'image=%s\n' "${IMAGE}" >>"${GITHUB_OUTPUT}"
fi
