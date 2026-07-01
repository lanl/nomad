#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
source "${SCRIPT_DIR}/../build_common.sh"

require_commands buildah git srun uv

PROJECT_DIR="${CI_PROJECT_DIR:-${NOMAD_DIR}}"
NOMAD_IMAGE_TAG="${NOMAD_IMAGE_TAG:-ci-${CI_JOB_ID}}"
WORK_DIR="${PROJECT_DIR}/${DEPLOY_NAME}_image_${NOMAD_IMAGE_TAG}"
LOG_DIR="${WORK_DIR}/logs"
OCI_DIR="${WORK_DIR}/oci"

mkdir -p "${LOG_DIR}" "${OCI_DIR}"

export NOMAD_PUSH=0
prepare_deploy_context

run_platform_build() {
    local arch="$1"
    local het_group="$2"
    local platform="$3"
    local image="${NOMAD_IMAGE_REPOSITORY}:${NOMAD_IMAGE_TAG}-${arch}"
    local archive="${OCI_DIR}/${arch}.oci.tar"
    local log="${LOG_DIR}/${arch}.log"

    echo "Starting ${platform} build on Slurm het-group ${het_group}" >&2
    srun \
        --het-group="${het_group}" \
        --nodes=1 \
        --ntasks=1 \
        --exclusive \
        env \
            CI_PROJECT_DIR="${PROJECT_DIR}" \
            NOMAD_BASE_IMAGE="${BASE_IMAGE}" \
            NOMAD_BUILDAH_SCRATCH="/tmp/nomad-buildah-${CI_JOB_ID}-${DEPLOY_NAME}-${arch}" \
            NOMAD_DEPLOY_CONTEXT_DIR="${DEPLOY_CONTEXT_DIR}" \
            NOMAD_DEPLOY_DIR="${DEPLOY_DIR}" \
            NOMAD_DEPLOY_NAME="${DEPLOY_NAME}" \
            NOMAD_IMAGE="${image}" \
            NOMAD_OCI_ARCHIVE="${archive}" \
            NOMAD_PLATFORMS="${platform}" \
            NOMAD_PREPARED_CONTEXT=1 \
            NOMAD_PUSH=0 \
            NOMAD_REGISTRY="${NOMAD_REGISTRY:-ghcr.io}" \
            bash -lc '
                ulimit -n 65535 || true
                rm -rf "${NOMAD_BUILDAH_SCRATCH}"
                mkdir -p "${NOMAD_BUILDAH_SCRATCH}"
                export BUILDAH_ROOT="${NOMAD_BUILDAH_SCRATCH}/storage"
                export BUILDAH_RUNROOT="${NOMAD_BUILDAH_SCRATCH}/runroot"
                export REGISTRY_AUTH_FILE="${NOMAD_BUILDAH_SCRATCH}/auth.json"
                cd "${CI_PROJECT_DIR}"
                ./container/deploy/build_buildah.sh
            ' \
        >"${log}" 2>&1
}

platforms=(
    "amd64 0 linux/amd64"
    "arm64 1 linux/arm64"
)
pids=()

for platform in "${platforms[@]}"; do
    read -r arch het_group target <<<"${platform}"
    run_platform_build "${arch}" "${het_group}" "${target}" &
    pids+=("${arch}:$!")
done

failed=0
for pid in "${pids[@]}"; do
    arch="${pid%%:*}"
    if ! wait "${pid#*:}"; then
        echo "${arch} build failed; log follows:" >&2
        sed "s/^/[${arch}] /" "${LOG_DIR}/${arch}.log" >&2 || true
        failed=1
    fi
done

for platform in "${platforms[@]}"; do
    read -r arch _ <<<"${platform}"
    sed "s/^/[${arch}] /" "${LOG_DIR}/${arch}.log"
done

if [ "${failed}" -ne 0 ]; then
    exit "${failed}"
fi

export NOMAD_AMD64_OCI="${OCI_DIR}/amd64.oci.tar"
export NOMAD_ARM64_OCI="${OCI_DIR}/arm64.oci.tar"
export NOMAD_IMAGE="${NOMAD_IMAGE_REPOSITORY}:${NOMAD_IMAGE_TAG}"

"${SCRIPT_DIR}/../push_oci_manifest.sh"
