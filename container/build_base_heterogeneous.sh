#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
source "${SCRIPT_DIR}/build_common.sh"

require_commands buildah git srun uv

PROJECT_DIR="${CI_PROJECT_DIR:-${NOMAD_DIR}}"
WORK_DIR="${PROJECT_DIR}/nomad_base_image"
LOG_DIR="${WORK_DIR}/logs"
OCI_DIR="${WORK_DIR}/oci"

mkdir -p "${LOG_DIR}" "${OCI_DIR}"

export NOMAD_PUSH=0
prepare_base_context

run_platform_build() {
    local arch="$1"
    local het_group="$2"
    local platform="$3"
    local image="${NOMAD_IMAGE_REPOSITORY}:ci-${CI_JOB_ID}-${arch}"
    local archive="${OCI_DIR}/${arch}.oci.tar"
    local log="${LOG_DIR}/${arch}.log"

    echo "Starting base ${platform} build on Slurm het-group ${het_group}" >&2
    srun \
        --het-group="${het_group}" \
        --nodes=1 \
        --ntasks=1 \
        --exclusive \
        env \
            CI_PROJECT_DIR="${PROJECT_DIR}" \
            NOMAD_BASE_CONTEXT_DIR="${BASE_CONTEXT_DIR}" \
            NOMAD_BUILDAH_SCRATCH="/tmp/nomad-base-buildah-${CI_JOB_ID}-${arch}" \
            NOMAD_IMAGE="${image}" \
            NOMAD_OCI_ARCHIVE="${archive}" \
            NOMAD_PLATFORMS="${platform}" \
            NOMAD_PREPARED_CONTEXT=1 \
            NOMAD_PUSH=0 \
            bash -lc '
                ulimit -n 65535 || true
                rm -rf "${NOMAD_BUILDAH_SCRATCH}"
                mkdir -p "${NOMAD_BUILDAH_SCRATCH}"
                export BUILDAH_ROOT="${NOMAD_BUILDAH_SCRATCH}/storage"
                export BUILDAH_RUNROOT="${NOMAD_BUILDAH_SCRATCH}/runroot"
                cd "${CI_PROJECT_DIR}"
                ./container/build_base_buildah.sh
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
        echo "${arch} base build failed; log follows:" >&2
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
export NOMAD_IMAGE="${NOMAD_IMAGE_REPOSITORY}:ci-${CI_JOB_ID}"

"${SCRIPT_DIR}/push_oci_manifest.sh"
