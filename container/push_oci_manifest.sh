#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
source "${SCRIPT_DIR}/build_common.sh"

BUILDAH="${BUILDAH:-buildah}"

require_commands "${BUILDAH}"
configure_buildah_command

"${BUILDAH_CMD[@]}" manifest create "${IMAGE}"
for platform in \
    "amd64 ${NOMAD_AMD64_OCI:?NOMAD_AMD64_OCI must point to an OCI archive}" \
    "arm64 ${NOMAD_ARM64_OCI:?NOMAD_ARM64_OCI must point to an OCI archive}"
do
    read -r arch archive <<<"${platform}"
    if [ ! -f "${archive}" ]; then
        echo "Missing ${arch} OCI archive: ${archive}" >&2
        exit 1
    fi
    "${BUILDAH_CMD[@]}" manifest add --os linux --arch "${arch}" "${IMAGE}" "oci-archive:${archive}"
done
push_args=(--all --format v2s2)
if [ -n "${REGISTRY_AUTH_FILE:-}" ]; then
    push_args+=(--authfile "${REGISTRY_AUTH_FILE}")
fi
"${BUILDAH_CMD[@]}" manifest push "${push_args[@]}" "${IMAGE}" "docker://${IMAGE}"
