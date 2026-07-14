set shell := ["bash", "-cu"]

default:
    @just --list

lint:
    uv run --group lint prek run --all-files

test *FLAGS:
    uv run --group test pytest --quiet --durations=0 {{ FLAGS }}

build-image:
    docker buildx build \
        --tag nomad-demo:latest \
        --file ./container/demo/Dockerfile \
        .

act workflow="" image="catthehacker/ubuntu:act-latest":
    set -- pull_request \
        --concurrent-jobs 1 \
        --platform "ubuntu-latest={{image}}"; \
    if [ "$(uname -o)" = "Darwin" ]; then \
        set -- "$@" --container-architecture linux/amd64; \
    fi; \
    if [ -n "{{workflow}}" ]; then \
        path=".github/workflows/{{workflow}}.yml"; \
        if [ ! -f "${path}" ]; then \
            echo "Workflow not found: ${path}" >&2; \
            exit 1; \
        fi; \
        set -- "$@" --workflows "${path}"; \
    fi; \
    command act "$@"

[arg("live", long="live", value="true")]
docs live="false":
    uv sync --directory ./container/demo
    if [ "{{ live }}" = "true" ]; then \
        make -C docs livehtml; \
    else \
        make -C docs; \
    fi
