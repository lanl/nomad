#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
source "${SCRIPT_DIR}/../build_common.sh"

BUILDAH="${BUILDAH:-buildah}"

require_commands git "${BUILDAH}"
if ! env_truthy "${NOMAD_PREPARED_CONTEXT:-0}"; then
    require_commands uv
fi

configure_buildah_command

echo "Buildah: $("${BUILDAH_CMD[@]}" --version)"
git --version
if ! env_truthy "${NOMAD_PREPARED_CONTEXT:-0}"; then
    uv --version
fi

prepare_or_print_deploy_context

build_args=()
create_git_credentials_build_args
build_args+=(--build-arg "NOMAD_BASE_IMAGE=${BASE_IMAGE}")
add_build_arg_from_env --platform NOMAD_PLATFORMS
add_build_arg_from_env --isolation BUILDAH_ISOLATION
add_build_arg_from_env --network BUILDAH_NETWORK

echo "Building image ${IMAGE}" >&2
"${BUILDAH_CMD[@]}" build \
    "${build_args[@]}" \
    "$@" \
    --manifest "${IMAGE}" \
    -f "${DEPLOY_DOCKERFILE}" \
    "${DEPLOY_CONTEXT_DIR}"

if env_truthy "${NOMAD_PUSH:-0}"; then
    push_args=(--all)
    if [ -n "${REGISTRY_AUTH_FILE:-}" ]; then
        push_args+=(--authfile "${REGISTRY_AUTH_FILE}")
    fi
    "${BUILDAH_CMD[@]}" manifest push "${push_args[@]}" "${IMAGE}" "docker://${IMAGE}"
fi

if [ -n "${NOMAD_OCI_ARCHIVE:-}" ]; then
    mkdir -p "$(dirname "${NOMAD_OCI_ARCHIVE}")"
    "${BUILDAH_CMD[@]}" manifest push --all "${IMAGE}" "oci-archive:${NOMAD_OCI_ARCHIVE}:${IMAGE}"
fi
