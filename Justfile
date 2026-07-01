set shell := ["bash", "-cu"]

default:
    @just --list

lint:
    uv run --group lint prek run --all-files

test *FLAGS:
    uv run --group test pytest --quiet --durations=0 {{FLAGS}}

[arg("live", long="live", value="true")]
docs live="false":
    if [ "{{ live }}" = "true" ]; then \
        make -C docs livehtml; \
    else \
        make -C docs; \
    fi
