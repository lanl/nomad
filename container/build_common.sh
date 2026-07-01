#!/usr/bin/env bash

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "build_common.sh is meant to be sourced by a builder script." >&2
    exit 1
fi

CONTAINER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
DEPLOY_SCRIPT_DIR="${CONTAINER_DIR}/deploy"
NOMAD_DIR="$(cd "${CONTAINER_DIR}/.." >/dev/null 2>&1 && pwd -P)"
DEPLOY_DIR="${NOMAD_DEPLOY_DIR:-${DEPLOY_SCRIPT_DIR}/${NOMAD_DEPLOY:-demo}}"
DEPLOY_DIR="$(cd "${DEPLOY_DIR}" >/dev/null 2>&1 && pwd -P)"
DEPLOY_NAME="${NOMAD_DEPLOY_NAME:-$(basename "${DEPLOY_DIR}")}"
PYTHON="${PYTHON:-3.12}"
TAG="${NOMAD_TAG:-"$(date +%Y%m%d)-g$(git -C "${NOMAD_DIR}" rev-parse --short HEAD)"}"
IMAGE="${NOMAD_IMAGE:-nomad-${DEPLOY_NAME}:${TAG}}"
BASE_IMAGE="${NOMAD_BASE_IMAGE:-nomad:latest}"
BASE_IMAGE_TAG="${NOMAD_BASE_IMAGE_TAG:-nomad:latest}"
BASE_CONTEXT_DIR="${NOMAD_BASE_CONTEXT_DIR:-${CONTAINER_DIR}/dist}"
DEPLOY_CONTEXT_DIR="${NOMAD_DEPLOY_CONTEXT_DIR:-${DEPLOY_DIR}/dist}"
DEPLOY_DOCKERFILE="${NOMAD_DEPLOY_DOCKERFILE:-${DEPLOY_DIR}/Dockerfile}"
BASE_DOCKERFILE="${NOMAD_BASE_DOCKERFILE:-${CONTAINER_DIR}/Dockerfile}"

require_commands() {
    local command

    for command in "$@"; do
        if ! command -v "${command}" >/dev/null 2>&1; then
            echo "Missing required command: ${command}" >&2
            exit 1
        fi
    done
}

print_build_context() {
    local context_dir="$1"

    echo "Build Context:" >&2
    if command -v sha256sum >/dev/null 2>&1; then
        find "${context_dir}" -type f -exec sha256sum '{}' \; >&2
    else
        find "${context_dir}" -type f -exec shasum -a 256 '{}' \; >&2
    fi
}

git_host() {
    printf '%s\n' "${NOMAD_GIT_HOST:-${CI_SERVER_HOST:-github.com}}"
}

create_git_credentials_secret() {
    local token
    local username
    local credential_source
    local secret_file

    if [ -n "${GIT_CREDENTIALS_SECRET_FILE:-}" ] && [ -f "${GIT_CREDENTIALS_SECRET_FILE}" ]; then
        return 0
    fi

    if [ -n "${NOMAD_GIT_TOKEN:-}" ]; then
        token="${NOMAD_GIT_TOKEN}"
        credential_source="NOMAD_GIT_TOKEN"
    elif [ -n "${GITLAB_TOKEN:-}" ]; then
        token="${GITLAB_TOKEN}"
        credential_source="GITLAB_TOKEN"
    elif [ -n "${GLAB_TOKEN:-}" ]; then
        token="${GLAB_TOKEN}"
        credential_source="GLAB_TOKEN"
    elif [ -n "${CI_JOB_TOKEN:-}" ]; then
        token="${CI_JOB_TOKEN}"
        credential_source="CI_JOB_TOKEN"
    elif [ -n "${GITHUB_TOKEN:-}" ]; then
        token="${GITHUB_TOKEN}"
        credential_source="GITHUB_TOKEN"
    else
        return 1
    fi

    if [ -n "${NOMAD_GIT_USER:-}" ]; then
        username="${NOMAD_GIT_USER}"
    elif [ "${credential_source}" = "CI_JOB_TOKEN" ]; then
        username="gitlab-ci-token"
    elif [ -n "${GITHUB_TOKEN:-}" ] && [ "${token}" = "${GITHUB_TOKEN}" ]; then
        username="x-access-token"
    else
        username="oauth2"
    fi

    secret_file="$(mktemp "${TMPDIR:-/tmp}/nomad-git-credentials.XXXXXX")"
    printf 'protocol=https\nhost=%s\nusername=%s\npassword=%s\n\n' \
        "$(git_host)" \
        "${username}" \
        "${token}" \
        | git credential-store --file="${secret_file}" store
    chmod 600 "${secret_file}"

    GIT_CREDENTIALS_SECRET_FILE="${secret_file}"
    trap 'rm -f "${GIT_CREDENTIALS_SECRET_FILE:-}"' EXIT
    echo "Using ${credential_source} credentials for git downloads from $(git_host)" >&2
}

create_git_credentials_build_args() {
    create_git_credentials_secret || return 0
    build_args+=(--secret "id=git-credentials,src=${GIT_CREDENTIALS_SECRET_FILE}")
}

configure_buildah_command() {
    local storage_opts
    local storage_opt

    BUILDAH_CMD=("${BUILDAH:-buildah}")
    if [ -n "${STORAGE_DRIVER:-}" ]; then
        BUILDAH_CMD+=(--storage-driver "${STORAGE_DRIVER}")
    fi
    if [ -n "${BUILDAH_STORAGE_OPTS:-}" ]; then
        read -r -a storage_opts <<<"${BUILDAH_STORAGE_OPTS}"
        for storage_opt in "${storage_opts[@]}"; do
            BUILDAH_CMD+=(--storage-opt "${storage_opt}")
        done
    fi
    if [ -n "${BUILDAH_ROOT:-}" ]; then
        BUILDAH_CMD+=(--root "${BUILDAH_ROOT}")
    fi
    if [ -n "${BUILDAH_RUNROOT:-}" ]; then
        BUILDAH_CMD+=(--runroot "${BUILDAH_RUNROOT}")
    fi
}

add_build_arg_from_env() {
    local flag="$1"
    local env_name="$2"
    local value="${!env_name:-}"

    if [ -n "${value}" ]; then
        build_args+=("${flag}" "${value}")
    fi
}

add_build_args_from_lines() {
    local flag="$1"
    local value

    while IFS= read -r value; do
        if [ -n "${value}" ]; then
            build_args+=("${flag}" "${value}")
        fi
    done
}

add_image_metadata_build_args() {
    if [ -n "${NOMAD_IMAGE_TAGS:-}" ]; then
        add_build_args_from_lines --tag <<<"${NOMAD_IMAGE_TAGS}"
    fi
    if [ -n "${NOMAD_IMAGE_LABELS:-}" ]; then
        add_build_args_from_lines --label <<<"${NOMAD_IMAGE_LABELS}"
    fi
}

first_line() {
    local value

    while IFS= read -r value; do
        if [ -n "${value}" ]; then
            printf '%s\n' "${value}"
            return 0
        fi
    done
    return 1
}

env_has_multiple_platforms() {
    case "${NOMAD_PLATFORMS:-}" in
        *,*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

configure_docker_buildx_platform() {
    add_build_arg_from_env --platform NOMAD_PLATFORMS
}

env_truthy() {
    case "${1:-}" in
        1 | true | TRUE | True | yes | YES | Yes | on | ON | On)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

prepare_or_print_deploy_context() {
    if env_truthy "${NOMAD_PREPARED_CONTEXT:-0}"; then
        echo "Using prepared build context in ${DEPLOY_CONTEXT_DIR}" >&2
        print_build_context "${DEPLOY_CONTEXT_DIR}"
    else
        prepare_deploy_context
    fi
}

prepare_or_print_base_context() {
    if env_truthy "${NOMAD_PREPARED_CONTEXT:-0}"; then
        echo "Using prepared build context in ${BASE_CONTEXT_DIR}" >&2
        print_build_context "${BASE_CONTEXT_DIR}"
    else
        prepare_base_context
    fi
}

prepare_python_build_environment() {
    export UV_MANAGED_PYTHON=1
    export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-.venv-$(uname -m)}"
    export UV_PYTHON="${UV_PYTHON:-$(uv python find --managed-python --system "${PYTHON}")}"
    export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "${NOMAD_DIR}" log -1 --format=%ct)}"
}

prepare_base_context() {
    prepare_python_build_environment

    rm -rf "${BASE_CONTEXT_DIR}"
    mkdir -p "${BASE_CONTEXT_DIR}/packages"

    cp "${CONTAINER_DIR}/git-credentials-k8-secrets.sh" "${BASE_CONTEXT_DIR}/"

    uv build \
        --quiet \
        --wheel \
        --directory "${NOMAD_DIR}" \
        --clear \
        --out-dir "${BASE_CONTEXT_DIR}/packages"

    print_build_context "${BASE_CONTEXT_DIR}"
}

prepare_deploy_context() {
    prepare_python_build_environment

    rm -rf "${DEPLOY_CONTEXT_DIR}"
    mkdir -p "${DEPLOY_CONTEXT_DIR}/models"

    cp "${DEPLOY_DIR}/pyproject.toml" "${DEPLOY_CONTEXT_DIR}/"
    if [ ! -f "${DEPLOY_DIR}/uv.lock" ]; then
        echo "Missing deployment lockfile: ${DEPLOY_DIR}/uv.lock" >&2
        exit 1
    fi
    cp "${DEPLOY_DIR}/uv.lock" "${DEPLOY_CONTEXT_DIR}/"
    if [ -d "${DEPLOY_DIR}/src" ]; then
        cp -R "${DEPLOY_DIR}/src" "${DEPLOY_CONTEXT_DIR}/"
        find "${DEPLOY_CONTEXT_DIR}/src" \
            \( -type d \( -name __pycache__ -o -name '*.egg-info' \) \
                -o -type f -name '*.py[co]' \) \
            -prune -exec rm -rf {} +
    fi

    if env_truthy "${NOMAD_INCLUDE_WEIGHTS:-0}"; then
        local export_args=(uv run nomad export --to "${NOMAD_EXPORT_TARGET:-disk}")
        local git_config_env=()
        if [ "${NOMAD_EXPORT_TARGET:-disk}" = "oras" ]; then
            if [ -z "${NOMAD_ORAS_REGISTRY:-}" ]; then
                echo "NOMAD_ORAS_REGISTRY is required when NOMAD_EXPORT_TARGET=oras" >&2
                exit 1
            fi
            export_args+=(--oras-registry "${NOMAD_ORAS_REGISTRY}")
        fi
        if create_git_credentials_secret; then
            git_config_env=(
                env
                GIT_CONFIG_COUNT=4
                GIT_CONFIG_KEY_0=credential.helper
                GIT_CONFIG_VALUE_0=
                GIT_CONFIG_KEY_1=credential.helper
                GIT_CONFIG_VALUE_1="store --file=${GIT_CREDENTIALS_SECRET_FILE}"
                GIT_CONFIG_KEY_2=credential.useHttpPath
                GIT_CONFIG_VALUE_2=false
                GIT_CONFIG_KEY_3=lfs.basictransfersonly
                GIT_CONFIG_VALUE_3=true
            )
        fi

        if ! (
            cd "${NOMAD_DIR}"
            "${git_config_env[@]}" \
                "${export_args[@]}" \
                "${DEPLOY_DIR}/nomad.yml" \
                "${DEPLOY_CONTEXT_DIR}"
        ); then
            echo "Failed to export deployment weights from ${DEPLOY_DIR}/nomad.yml" >&2
            exit 1
        fi
    else
        cp "${DEPLOY_DIR}/nomad.yml" "${DEPLOY_CONTEXT_DIR}/"
    fi

    touch "${DEPLOY_CONTEXT_DIR}/models/.gitkeep"
    print_build_context "${DEPLOY_CONTEXT_DIR}"
}

build_image_ref() {
    if [ -n "${NOMAD_IMAGE_REPOSITORY:-}" ]; then
        printf '%s:%s\n' "${NOMAD_IMAGE_REPOSITORY}" "${NOMAD_IMAGE_TAG:-ci-${CI_JOB_ID:-local}}"
    else
        printf '%s\n' "${IMAGE}"
    fi
}
