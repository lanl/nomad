import base64
import json
import os
import sys
import types
from hashlib import blake2b
from pathlib import Path
from types import SimpleNamespace

import pytest

from nomad import hub

ORAS_DIGEST = "sha256:" + "d" * 64


def test_get_cache_root_uses_xdg_env(monkeypatch, tmp_path):
    cache_root = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))
    assert hub.get_cache_root() == cache_root / "nomad"


def test_get_cache_root_uses_env(monkeypatch, tmp_path):
    cache_root = tmp_path / "nomad-cache"
    monkeypatch.setenv("NOMAD_CACHE", str(cache_root))
    assert hub.get_cache_root() == cache_root


def test_get_cache_root_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert hub.get_cache_root() == tmp_path / ".cache" / "nomad"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a" * 40, True),
        ("A" * 64, True),
        ("sha1:" + "a" * 40, True),
        ("sha256:" + "a" * 64, True),
        ("sha265:" + "a" * 64, True),
        ("sha512:" + "a" * 128, True),
        ("sha256:deadbeef", False),
        ("md5:" + "a" * 32, False),
        ("g" * 40, False),
        ("a" * 39, False),
        ("main", False),
    ],
)
def test_looks_like_digest(value: str, expected: bool):
    assert hub.looks_like_digest(value) is expected


@pytest.fixture
def registry_auth_paths(tmp_path):
    nomad_auth_file = tmp_path / "nomad-auth.json"
    nomad_auth_file.write_text('{"auths": {}}', encoding="utf-8")
    docker_config_dir = tmp_path / "docker"
    docker_config_dir.mkdir()
    docker_config = docker_config_dir / "config.json"
    docker_config.write_text("{}", encoding="utf-8")
    return {
        "NOMAD_AUTH_FILE": nomad_auth_file,
        "DOCKER_CONFIG": docker_config_dir,
        "DOCKER_CONFIG_FILE": docker_config,
    }


@pytest.mark.parametrize(
    ("env_names", "expected_key"),
    [
        pytest.param(
            ("NOMAD_AUTH_FILE", "DOCKER_CONFIG"),
            "NOMAD_AUTH_FILE",
            id="NOMAD_AUTH_FILE",
        ),
        pytest.param(("DOCKER_CONFIG",), "DOCKER_CONFIG_FILE", id="DOCKER_CONFIG"),
    ],
)
def test__get_docker_config_path_uses_first_existing_config_env(
    monkeypatch, registry_auth_paths, env_names, expected_key
):
    for env_name in env_names:
        monkeypatch.setenv(env_name, str(registry_auth_paths[env_name]))

    config_path = hub._get_docker_config_path()
    if expected_key == "NOMAD_AUTH_FILE":
        assert config_path is not None
        assert json.loads(Path(config_path).read_text(encoding="utf-8")) == {
            "auths": {}
        }
    else:
        assert config_path == str(registry_auth_paths[expected_key])


def test__get_docker_config_path_errors_when_nomad_auth_file_is_missing(
    monkeypatch, tmp_path, registry_auth_paths
):
    missing_auth_file = tmp_path / "missing-nomad-auth.json"
    monkeypatch.setenv("NOMAD_AUTH_FILE", str(missing_auth_file))
    monkeypatch.setenv("DOCKER_CONFIG", str(registry_auth_paths["DOCKER_CONFIG"]))

    with pytest.raises(FileNotFoundError, match="NOMAD_AUTH_FILE"):
        hub._get_docker_config_path()


def test__get_docker_config_path_falls_back_when_docker_config_is_missing(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "missing-docker-config"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    assert hub._get_docker_config_path() is None


@pytest.mark.parametrize(
    ("auths", "target_ref", "expected_basic_auth", "warns"),
    [
        pytest.param(
            {
                "registry.example.com/team-a/models": {
                    "username": "team-a",
                    "password": "secret",
                }
            },
            "registry.example.com/team-b/models:v1",
            base64.b64encode(b"team-a:secret").decode(),
            True,
            id="repo-scoped-domain-fallback",
        ),
        pytest.param(
            {
                "registry.example.com/team-a/models": {
                    "username": "team-a",
                    "password": "secret",
                },
                "registry.example.com": {
                    "username": "host",
                    "password": "secret",
                },
            },
            "registry.example.com/team-b/models:v1",
            base64.b64encode(b"host:secret").decode(),
            False,
            id="host-before-domain-fallback",
        ),
        pytest.param(
            {
                "registry.example.com/team/models": {
                    "auth": base64.b64encode(b"encoded:secret").decode()
                }
            },
            "registry.example.com/team/models:v1",
            base64.b64encode(b"encoded:secret").decode(),
            False,
            id="encoded-auth",
        ),
    ],
)
def test_oras_registry_load_auth_config_logs_in_matching_auth(
    auths,
    target_ref,
    expected_basic_auth,
    warns,
    caplog,
    tmp_path,
):
    auth_file = tmp_path / "nomad-auth.json"
    auth_file.write_text(json.dumps({"auths": auths}), encoding="utf-8")
    registry = hub.OrasRegistry()

    with caplog.at_level("WARNING", logger="nomad"):
        registry.load_auth_config(target_ref, config_path=str(auth_file))

    assert registry.auth._basic_auth == expected_basic_auth
    assert ("domain-level fallback" in caplog.text) is warns


def test__get_docker_config_path_uses_nomad_auth_file_as_is(monkeypatch, tmp_path):
    auth_file = tmp_path / "nomad-auth.json"
    auth_file.write_text(json.dumps({"auths": {}}), encoding="utf-8")
    monkeypatch.setenv("NOMAD_AUTH_FILE", str(auth_file))

    assert hub._get_docker_config_path("registry.example.com/team/model:v1") == str(
        auth_file
    )


def test_oras_registry_load_auth_config_uses_config_path(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"auths": {"registry.example.com": {"auth": "abc123"}}}),
        encoding="utf-8",
    )
    calls = []

    def fake_load_configs(self, container, configs=None):
        calls.append((str(container), configs))

    monkeypatch.setattr(
        type(hub.OrasRegistry().auth), "load_configs", fake_load_configs
    )

    registry = hub.OrasRegistry()
    container = registry.get_container("registry.example.com/org/model:v1")
    registry.load_auth_config(container, config_path=str(config_path))

    assert calls == [("registry.example.com/org/model:v1", [str(config_path)])]


def test_oras_registry_pull_preloads_matching_auth_config(monkeypatch, tmp_path):
    encoded_auth = base64.b64encode(b"runner:secret").decode()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "auths": {
                    "registry.example.com/team": {"auth": encoded_auth},
                }
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_pull(self, target, config_path=None, **kwargs):
        calls.append((target, config_path, self.auth._basic_auth))
        return []

    monkeypatch.setattr(hub.Registry, "pull", fake_pull)

    registry = hub.OrasRegistry()
    target = f"registry.example.com/team/models@{ORAS_DIGEST}"
    registry.pull(target, config_path=str(config_path), outdir=str(tmp_path / "out"))

    assert calls == [(target, None, encoded_auth)]


def test_oras_registry_pull_uses_nomad_auth_file_when_config_path_omitted(
    monkeypatch, tmp_path
):
    encoded_auth = base64.b64encode(b"runner:secret").decode()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "auths": {
                    "registry.example.com/team": {"auth": encoded_auth},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NOMAD_AUTH_FILE", str(config_path))
    calls = []

    def fake_pull(self, target, config_path=None, **kwargs):
        calls.append((target, config_path, self.auth._basic_auth))
        return []

    monkeypatch.setattr(hub.Registry, "pull", fake_pull)

    registry = hub.OrasRegistry()
    target = f"registry.example.com/team/models@{ORAS_DIGEST}"
    registry.pull(target, outdir=str(tmp_path / "out"))

    assert calls == [(target, None, encoded_auth)]


def test_oras_registry_push_preloads_matching_auth_config(monkeypatch, tmp_path):
    encoded_auth = base64.b64encode(b"runner:secret").decode()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "auths": {
                    "registry.example.com/team": {"auth": encoded_auth},
                }
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_push(self, target, config_path=None, **kwargs):
        calls.append((target, config_path, self.auth._basic_auth))
        return SimpleNamespace(headers={"Docker-Content-Digest": ORAS_DIGEST})

    monkeypatch.setattr(hub.Registry, "push", fake_push)

    registry = hub.OrasRegistry()
    target = "registry.example.com/team/models:v1"
    registry.push(target, config_path=str(config_path), files=[str(tmp_path / "model")])

    assert calls == [(target, None, encoded_auth)]


def test_oras_registry_push_uses_nomad_auth_file_when_config_path_omitted(
    monkeypatch, tmp_path
):
    encoded_auth = base64.b64encode(b"runner:secret").decode()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "auths": {
                    "registry.example.com/team": {"auth": encoded_auth},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NOMAD_AUTH_FILE", str(config_path))
    calls = []

    def fake_push(self, target, config_path=None, **kwargs):
        calls.append((target, config_path, self.auth._basic_auth))
        return SimpleNamespace(headers={"Docker-Content-Digest": ORAS_DIGEST})

    monkeypatch.setattr(hub.Registry, "push", fake_push)

    registry = hub.OrasRegistry()
    target = "registry.example.com/team/models:v1"
    registry.push(target, files=[str(tmp_path / "model")])

    assert calls == [(target, None, encoded_auth)]


def test_file_repospec_push_copies_source(monkeypatch, tmp_path):
    source_path = tmp_path / "source-model"
    destination = tmp_path / "bundle" / "models" / "source-model"
    source_path.mkdir()
    calls: list[tuple[Path, Path, bool]] = []

    def fake_copy_cow(source: Path, target: Path, symlinks: bool = True):
        calls.append((source, target, symlinks))
        target.mkdir(parents=True)

    monkeypatch.setattr(hub, "copy_cow", fake_copy_cow)

    destination_spec = hub.FileRepoSpec(scheme="file", location=str(destination))

    assert destination_spec.push(source_path) == destination_spec
    assert calls == [(source_path, destination, True)]


def test_oras_repospec_push_uses_sdk_and_returns_pinned_spec(monkeypatch, tmp_path):
    source_path = tmp_path / "source-model"
    source_path.mkdir()
    (source_path / "config.json").write_text("{}", encoding="utf-8")
    (source_path / "weights.safetensors").write_text("weights", encoding="utf-8")
    calls: dict[str, object] = {}

    class FakeRegistry:
        def push(
            self,
            target,
            *,
            config_path=None,
            disable_path_validation=False,
            files=None,
        ):
            calls["push"] = (
                target,
                config_path,
                disable_path_validation,
                [Path(item).name for item in files],
            )
            return SimpleNamespace(headers={"Docker-Content-Digest": "sha256:deadbeef"})

    monkeypatch.setattr(
        hub.OrasRepoSpec, "_registry", staticmethod(lambda: FakeRegistry())
    )

    pushed = hub.RepoSpec.parse("oras://registry.example.com/scifm:weather-v1").push(
        source_path
    )

    assert pushed.uri() == "oras://registry.example.com/scifm@sha256:deadbeef"
    assert calls == {
        "push": (
            "registry.example.com/scifm:weather-v1",
            None,
            True,
            ["config.json", "weights.safetensors"],
        ),
    }


def test_oras_repospec_push_requires_directory(tmp_path: Path):
    source_path = tmp_path / "source-model.safetensors"
    source_path.write_text("weights", encoding="utf-8")

    with pytest.raises(ValueError, match="ORAS model export requires a directory"):
        hub.RepoSpec.parse("oras://registry.example.com/scifm:weather-v1").push(
            source_path
        )


def test_oras_repospec_push_reports_artifact_on_sdk_error(monkeypatch, tmp_path: Path):
    source_path = tmp_path / "source-model"
    source_path.mkdir()
    monkeypatch.delenv("DOCKER_CONFIG", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    class FakeRegistry:
        def push(
            self,
            target,
            *,
            config_path=None,
            disable_path_validation=False,
            files=None,
        ):
            raise ValueError("unauthorized")

    monkeypatch.setattr(
        hub.OrasRepoSpec, "_registry", staticmethod(lambda: FakeRegistry())
    )

    with pytest.raises(RuntimeError) as exc:
        hub.RepoSpec.parse("oras://registry.example.com/scifm:weather-v1").push(
            source_path
        )

    assert (
        "Could not push model 'source-model' to ORAS artifact "
        "'registry.example.com/scifm:weather-v1': unauthorized"
    ) in str(exc.value)


def test_oras_registry_retries_with_fresh_token(monkeypatch):
    responses = [
        SimpleNamespace(status_code=401, headers={"Www-Authenticate": "Bearer"}),
        SimpleNamespace(status_code=200, headers={}),
    ]
    tokens: list[str | None] = []

    def fake_do_request(
        self, url, method="GET", data=None, headers=None, json=None, stream=False
    ):
        tokens.append(self.auth.token)
        return responses.pop(0)

    monkeypatch.setattr(hub.Registry, "do_request", fake_do_request)
    registry = hub.OrasRegistry()
    registry.auth.token = "stale"

    response = registry.do_request("https://registry.example.com/v2/upload")

    assert response.status_code == 200
    assert tokens == ["stale", None]


def test_run_returns_captured_stdout(monkeypatch):
    def fake_run(cmd, cwd=None, capture_output=False, text=False, **kwargs):
        assert cmd == ["echo", "hello"]
        assert capture_output is True
        assert text is True
        assert kwargs == {"check": True}
        return SimpleNamespace(returncode=0, stdout="hello\n")

    monkeypatch.setattr(hub.subprocess, "run", fake_run)
    assert hub.run(["echo", "hello"], capture=True) == "hello"


def test_run_raises_on_failure(monkeypatch, caplog):
    def fake_run(
        cmd,
        cwd=None,
        capture_output=False,
        text=False,
        stdout=None,
        stderr=None,
        **kwargs,
    ):
        assert capture_output is False
        assert stdout is hub.subprocess.DEVNULL
        assert stderr is hub.subprocess.PIPE
        assert kwargs == {"check": True}
        raise hub.subprocess.CalledProcessError(
            returncode=2,
            cmd=cmd,
            stderr="boom",
        )

    monkeypatch.setattr(hub.subprocess, "run", fake_run)
    with pytest.raises(hub.subprocess.CalledProcessError) as exc:
        hub.run(["false"])

    assert exc.value.returncode == 2
    assert "returned non-zero exit status 2" in caplog.text
    assert "boom" in caplog.text


def test_run_merges_extra_environment(monkeypatch):
    def fake_run(cmd, cwd=None, text=False, env=None, **kwargs):
        assert cmd == ["env"]
        assert env is not None
        assert env["NOMAD_TEST_ENV"] == "ready"
        assert kwargs["check"] is True
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(hub.subprocess, "run", fake_run)

    hub.run(["env"], env={"NOMAD_TEST_ENV": "ready"})


def test_run_raises_when_command_missing(monkeypatch, caplog):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(hub.subprocess, "run", fake_run)
    with pytest.raises(FileNotFoundError):
        hub.run(["oras", "pull"])

    assert "oras not found on PATH" in caplog.text


def test_check_dependencies_missing_git(monkeypatch):
    monkeypatch.setattr(hub.shutil, "which", lambda _: None)
    with pytest.raises(SystemExit) as exc:
        hub.check_dependencies()
    assert "git not found" in str(exc.value)


def test_check_dependencies_missing_git_lfs(monkeypatch):
    monkeypatch.setattr(hub.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(
        hub.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    with pytest.raises(SystemExit) as exc:
        hub.check_dependencies()
    assert "git-lfs not installed" in str(exc.value)


def test_repospec_parse_missing_scheme_prefers_existing_file(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    spec = hub.RepoSpec.parse("model", base_dir=tmp_path)

    assert spec.scheme == "file"
    assert spec.location == str(model_dir)
    assert spec.uri() == model_dir.as_uri()


def test_repospec_parse_missing_scheme_falls_back_to_hf(tmp_path):
    spec = hub.RepoSpec.parse("org/model", base_dir=tmp_path)

    assert spec.scheme == "hf"
    assert spec.location == "org/model"
    assert spec.uri() == "hf://org/model"


def test_repospec_normalize_missing_scheme_prefers_existing_file(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    assert hub.RepoSpec.normalize("model", base_dir=tmp_path) == model_dir.as_uri()


def test_repospec_normalize_existing_file_with_relative_base_dir(monkeypatch, tmp_path):
    model_dir = tmp_path / "bundle" / "models" / "model"
    model_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert (
        hub.RepoSpec.normalize("models/model", base_dir=Path("bundle"))
        == model_dir.as_uri()
    )


def test_repospec_normalize_missing_scheme_falls_back_to_hf(tmp_path):
    assert hub.RepoSpec.normalize("org/model", base_dir=tmp_path) == "hf://org/model"


@pytest.mark.parametrize(
    "source, normalized",
    [
        (
            "http://example.com/org/repo.git@main#weights",
            "git+https://example.com/org/repo.git@main#weights",
        ),
        (
            "https://example.com/org/repo.git#weights",
            "git+https://example.com/org/repo.git#weights",
        ),
        (
            "ssh://git@example.com:2222/org/repo.git@main#weights",
            "git+ssh://git@example.com:2222/org/repo.git@main#weights",
        ),
        (
            "git@example.com:org/repo.git@main#weights",
            "git+ssh://git@example.com/org/repo.git@main#weights",
        ),
    ],
)
def test_repospec_normalize_compatibility_git_urls(source: str, normalized: str):
    assert hub.RepoSpec.normalize(source) == normalized


def test_repospec_parse_file_uri_with_relative_path(tmp_path):
    spec = hub.RepoSpec.parse("file:models/demo#weights", base_dir=tmp_path)

    assert spec.scheme == "file"
    assert spec.location == str(tmp_path / "models" / "demo")
    assert spec.subpath == "weights"


def test_file_repospec_uri_can_render_relative_to_base_dir(tmp_path):
    spec = hub.RepoSpec.parse((tmp_path / "bundle" / "models" / "demo").as_uri())

    assert spec.uri(base_dir=tmp_path / "bundle") == "file:models/demo"


@pytest.mark.parametrize(
    "source, scheme, location, reference, subpath",
    [
        ("hf://org/model@abc123#weights", "hf", "org/model", "abc123", "weights"),
        (
            "oras://registry.example.com/org/model:v1#weights",
            "oras",
            "registry.example.com/org/model:v1",
            None,
            "weights",
        ),
        (
            f"oras://registry.example.com/org/model@{ORAS_DIGEST}",
            "oras",
            "registry.example.com/org/model",
            ORAS_DIGEST,
            None,
        ),
        (
            "git+https://example.com/org/repo.git@main#weights",
            "git+https",
            "https://example.com/org/repo.git",
            "main",
            "weights",
        ),
        (
            "git+ssh://git@example.com:2222/org/repo.git@feature/refactor",
            "git+ssh",
            "ssh://git@example.com:2222/org/repo.git",
            "feature/refactor",
            None,
        ),
        (
            "https://example.com/org/repo.git@main#weights",
            "git+https",
            "https://example.com/org/repo.git",
            "main",
            "weights",
        ),
        (
            "http://example.com/org/repo.git@main#weights",
            "git+https",
            "https://example.com/org/repo.git",
            "main",
            "weights",
        ),
        (
            "git@example.com:org/repo.git@main#weights",
            "git+ssh",
            "ssh://git@example.com/org/repo.git",
            "main",
            "weights",
        ),
        (
            "ssh://git@example.com:2222/org/repo.git@main#weights",
            "git+ssh",
            "ssh://git@example.com:2222/org/repo.git",
            "main",
            "weights",
        ),
    ],
)
def test_repospec_parse_supported_uris(
    source: str,
    scheme: str,
    location: str,
    reference: str | None,
    subpath: str | None,
):
    spec = hub.RepoSpec.parse(source)

    assert spec.scheme == scheme
    assert spec.location == location
    assert spec.reference == reference
    assert spec.subpath == subpath


@pytest.mark.parametrize(
    "source",
    [
        "",
        "oras://registry.example.com",
        "oras://registry.example.com/org/model@sha256:deadbeef",
    ],
)
def test_repospec_parse_rejects_unsupported_or_incomplete_inputs(source: str):
    with pytest.raises(ValueError):
        hub.RepoSpec.parse(source)


def test_repospec_uri_round_trips_supported_remote_uri():
    source = "git+https://example.com/org/repo.git@main#weights"
    assert hub.RepoSpec.parse(source).uri() == source


def test_repospec_as_https_rewrites_git_ssh():
    spec = hub.RepoSpec.parse("git+ssh://git@example.com:2222/org/repo.git@main#model")

    assert spec.as_https().uri() == "git+https://example.com/org/repo.git@main#model"


def test_repospec_resolved_resolves_git(monkeypatch):
    commit = "d" * 40
    monkeypatch.setattr(hub.GitRepoSpec, "cache_digest", lambda self: commit)

    assert (
        hub.RepoSpec.parse("git+https://example.com/org/repo.git@main#weights")
        .resolved()
        .uri()
        == f"git+https://example.com/org/repo.git@{commit}#weights"
    )


def test_repospec_resolved_resolves_oras(monkeypatch):
    monkeypatch.setattr(hub.OrasRepoSpec, "_digest", lambda self: ORAS_DIGEST)

    assert (
        hub.RepoSpec.parse("oras://registry.example.com/org/model:v1#weights")
        .resolved()
        .uri()
        == f"oras://registry.example.com/org/model@{ORAS_DIGEST}#weights"
    )


def test_repospec_resolved_resolves_hf(monkeypatch):
    monkeypatch.setattr(hub.HuggingFaceRepoSpec, "cache_digest", lambda self: "abc123")

    assert (
        hub.RepoSpec.parse("hf://org/model").resolved().uri() == "hf://org/model@abc123"
    )


def test_repospec_lock_returns_resolved(monkeypatch):
    commit = "d" * 40
    monkeypatch.setattr(hub.GitRepoSpec, "cache_digest", lambda self: commit)

    assert (
        hub.RepoSpec.lock("git+https://example.com/org/repo.git@main")
        == f"git+https://example.com/org/repo.git@{commit}"
    )


def test_repospec_pull_file_returns_subpath(tmp_path):
    model_dir = tmp_path / "model"
    weights_dir = model_dir / "weights"
    weights_dir.mkdir(parents=True)

    spec = hub.RepoSpec.parse("file:model#weights", base_dir=tmp_path)

    assert spec.pull() == weights_dir


def test_repospec_pull_hf_uses_snapshot_download(monkeypatch):
    calls = []
    revision = "a" * 40

    def fake_snapshot_download(repo_id, revision):
        calls.append((repo_id, revision))
        return "/tmp/hf-cache"

    fake_hf = types.SimpleNamespace(snapshot_download=fake_snapshot_download)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    spec = hub.RepoSpec.parse(f"hf://org/model@{revision}")

    assert spec.pull() == Path("/tmp/hf-cache")
    assert calls == [("org/model", revision)]


def test_repospec_hf_cache_digest_uses_model_revision(monkeypatch):
    class FakeHfApi:
        def model_info(self, **kwargs):
            assert kwargs == {"repo_id": "org/model"}
            return SimpleNamespace(sha="abc123")

    fake_hf = types.SimpleNamespace(HfApi=FakeHfApi)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    spec = hub.RepoSpec.parse("hf://org/model")
    assert isinstance(spec, hub.HuggingFaceRepoSpec)
    assert spec.cache_digest() == "abc123"


def test_repospec_hf_cache_digest_resolves_explicit_reference(monkeypatch):
    class FakeHfApi:
        def model_info(self, **kwargs):
            assert kwargs == {"repo_id": "org/model", "revision": "main"}
            return SimpleNamespace(sha="abc123")

    fake_hf = types.SimpleNamespace(HfApi=FakeHfApi)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    spec = hub.RepoSpec.parse("hf://org/model@main")
    assert isinstance(spec, hub.HuggingFaceRepoSpec)
    assert spec.resolved().uri() == "hf://org/model@abc123"


def test_repospec_hf_cache_digest_keeps_pinned_revision(monkeypatch):
    revision = "a" * 40

    class FakeHfApi:
        def model_info(self, **kwargs):
            raise AssertionError("pinned revisions should not call Hugging Face")

    fake_hf = types.SimpleNamespace(HfApi=FakeHfApi)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    spec = hub.RepoSpec.parse(f"hf://org/model@{revision}")
    assert isinstance(spec, hub.HuggingFaceRepoSpec)
    assert spec.cache_digest() == revision


def test_repospec_repo_key_is_hash_based_for_git():
    spec = hub.RepoSpec.parse("git+https://example.com/org/repo.git")
    expected_hash = blake2b(b"example.com/org/repo.git", digest_size=8).hexdigest()

    assert isinstance(spec, hub.GitRepoSpec)
    assert spec.repo_key() == f"repo--{expected_hash}"


def test_repospec_repo_key_matches_git_https_and_ssh_variants():
    https_spec = hub.RepoSpec.parse("git+https://example.com/org/repo.git")
    ssh_spec = hub.RepoSpec.parse("git+ssh://git@example.com:2222/org/repo.git")

    assert isinstance(https_spec, hub.GitRepoSpec)
    assert isinstance(ssh_spec, hub.GitRepoSpec)
    assert https_spec.repo_key() == ssh_spec.repo_key()


def test_repospec_git_cache_digest_uses_ls_remote(monkeypatch):
    calls: list[tuple[list[str], Path | None, bool]] = []

    def fake_run(cmd, cwd=None, capture=False):
        calls.append((cmd, cwd, capture))
        return "abcdef1234567890\trefs/heads/main"

    monkeypatch.setattr(hub, "run", fake_run)

    spec = hub.RepoSpec.parse("git+https://example.com/repo.git@main")
    assert isinstance(spec, hub.GitRepoSpec)
    commit = spec.cache_digest()

    assert commit == "abcdef1234567890"
    assert calls == [
        (["git", "ls-remote", "https://example.com/repo.git", "main"], None, True)
    ]


def test_repospec_git_cache_digest_keeps_pinned_commit(monkeypatch):
    commit = "a" * 40

    def fake_run(*args, **kwargs):
        raise AssertionError("pinned commits should not call git ls-remote")

    monkeypatch.setattr(hub, "run", fake_run)

    spec = hub.RepoSpec.parse(f"git+https://example.com/repo.git@{commit}")
    assert isinstance(spec, hub.GitRepoSpec)
    assert spec.cache_digest() == commit


def test_repospec_git_cache_digest_exits_when_missing(monkeypatch):
    monkeypatch.setattr(hub, "run", lambda *args, **kwargs: "")
    spec = hub.RepoSpec.parse("git+https://example.com/repo.git@main")
    assert isinstance(spec, hub.GitRepoSpec)
    with pytest.raises(SystemExit) as exc:
        spec.cache_digest()
    assert "Could not resolve ref 'main'" in str(exc.value)


def test_repospec_git_lfs_pull_command_scopes_to_subpath():
    spec = hub.RepoSpec.parse("git+https://example.com/repo.git#models/demo")
    assert isinstance(spec, hub.GitRepoSpec)

    assert spec._lfs_pull_cmd() == [
        "git",
        "lfs",
        "pull",
        "--include",
        "models/demo,models/demo/**",
    ]


def test_repospec_git_lfs_pull_command_pulls_all_without_subpath():
    spec = hub.RepoSpec.parse("git+https://example.com/repo.git")
    assert isinstance(spec, hub.GitRepoSpec)

    assert spec._lfs_pull_cmd() == ["git", "lfs", "pull"]


def test_pull_git_reuses_cached_clone(monkeypatch, tmp_path):
    spec = hub.RepoSpec.parse("git+https://example.com/repo.git")
    commit = "a" * 40
    cache_root = tmp_path / "cache"
    assert isinstance(spec, hub.GitRepoSpec)
    cache_dir = cache_root / "git+https" / spec.repo_key() / commit
    cache_dir.mkdir(parents=True)

    monkeypatch.setattr(hub.GitRepoSpec, "cache_digest", lambda self: commit)
    monkeypatch.setattr(hub, "CACHE_ROOT", cache_root)

    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return ""

    monkeypatch.setattr(hub, "run", fake_run)

    assert spec.pull() == cache_dir
    assert called is False


def test_pull_git_clones_when_missing(monkeypatch, tmp_path):
    spec = hub.RepoSpec.parse("git+https://example.com/repo.git")
    commit = "d" * 40
    cache_root = tmp_path / "cache"
    assert isinstance(spec, hub.GitRepoSpec)
    monkeypatch.setattr(hub.GitRepoSpec, "cache_digest", lambda self: commit)
    monkeypatch.setattr(hub, "CACHE_ROOT", cache_root)

    repo_key = spec.repo_key()
    cache_dir_tmp = cache_root / "git+https" / repo_key / f"tmp--{commit}"
    calls: list[tuple[list[str], Path | None, bool, dict[str, str] | None]] = []

    def fake_run(cmd, cwd=None, capture=False, env=None):
        calls.append((cmd, cwd, capture, env))
        if cmd == ["git", "init", "-q"]:
            cache_dir_tmp.mkdir(parents=True, exist_ok=True)
        return ""

    monkeypatch.setattr(hub, "run", fake_run)

    cache_dir = spec.pull()
    expected_dir = cache_root / "git+https" / repo_key / commit

    assert cache_dir == expected_dir
    assert expected_dir.exists()
    assert not cache_dir_tmp.exists()
    assert calls == [
        (["git", "init", "-q"], cache_dir_tmp, False, None),
        (
            ["git", "remote", "add", "origin", "https://example.com/repo.git"],
            cache_dir_tmp,
            False,
            None,
        ),
        (
            ["git", "fetch", "--depth", "1", "origin", commit],
            cache_dir_tmp,
            False,
            None,
        ),
        (
            ["git", "checkout", commit],
            cache_dir_tmp,
            False,
            {"GIT_LFS_SKIP_SMUDGE": "1"},
        ),
        (["git", "lfs", "pull"], cache_dir_tmp, False, None),
    ]


def test_pull_git_reports_lock_timeout(monkeypatch, tmp_path):
    spec = hub.RepoSpec.parse("git+https://example.com/repo.git")
    commit = "d" * 40
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(hub.GitRepoSpec, "cache_digest", lambda self: commit)
    monkeypatch.setattr(hub, "CACHE_ROOT", cache_root)

    class FakeFileLock:
        def __init__(self, lock_path, timeout):
            self.lock_path = lock_path

        def __enter__(self):
            raise hub.Timeout(str(self.lock_path))

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(hub, "FileLock", FakeFileLock)

    with pytest.raises(TimeoutError, match="Timed out waiting for cache lock"):
        spec.pull()


def test_repospec_oras_resolved_uses_existing_digest(monkeypatch):
    called = False

    def fake_registry():
        nonlocal called
        called = True
        raise AssertionError("registry should not be used for pinned digests")

    monkeypatch.setattr(hub.OrasRepoSpec, "_registry", staticmethod(fake_registry))

    spec = hub.RepoSpec.parse(f"oras://registry.example.com/models/model@{ORAS_DIGEST}")
    assert isinstance(spec, hub.OrasRepoSpec)
    assert spec.resolved().reference == ORAS_DIGEST
    assert spec.cache_digest() == ORAS_DIGEST.replace(":", "-")
    assert called is False


def test_repospec_oras_resolved_uses_oras_resolve(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.delenv("DOCKER_CONFIG", raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path("/missing-home"))

    class FakeContainer:
        def __str__(self):
            return "registry.example.com/models/model:v1"

        def manifest_url(self):
            return "registry.example.com/v2/models/model/manifests/v1"

    class FakeRegistry:
        prefix = "https"

        def get_container(self, target):
            calls.append(("container", target, {}))
            return FakeContainer()

        def request(self, target, url, method="GET", headers=None, **kwargs):
            calls.append(("request", target, url, method, headers or {}))
            return SimpleNamespace(
                status_code=200,
                headers={"Docker-Content-Digest": ORAS_DIGEST},
            )

    monkeypatch.setattr(
        hub.OrasRepoSpec, "_registry", staticmethod(lambda: FakeRegistry())
    )

    spec = hub.RepoSpec.parse("oras://registry.example.com/models/model:v1")
    assert isinstance(spec, hub.OrasRepoSpec)
    assert spec.resolved().reference == ORAS_DIGEST
    assert calls[0] == ("container", "registry.example.com/models/model:v1", {})
    _, target, url, method, headers = calls[1]
    assert target == "registry.example.com/models/model:v1"
    assert method == "HEAD"
    assert url == "https://registry.example.com/v2/models/model/manifests/v1"
    assert "application/vnd.oci.image.manifest.v1+json" in headers["Accept"]
    assert "application/vnd.docker.distribution.manifest.v2+json" in headers["Accept"]


def test_pull_oras_reuses_cache(monkeypatch, tmp_path):
    spec = hub.RepoSpec.parse("oras://registry.example.com/models/model:v1")
    digest = ORAS_DIGEST
    cache_root = tmp_path / "cache"
    assert isinstance(spec, hub.OrasRepoSpec)
    cache_dir = cache_root / "oras" / spec.repo_key() / spec._digest_cache_name(digest)
    cache_dir.mkdir(parents=True)

    monkeypatch.setattr(hub.OrasRepoSpec, "_digest", lambda self: digest)
    monkeypatch.setattr(hub, "CACHE_ROOT", cache_root)

    called = False

    def fake_registry():
        nonlocal called
        called = True
        raise AssertionError("registry should not be used for cached artifacts")

    monkeypatch.setattr(hub.OrasRepoSpec, "_registry", staticmethod(fake_registry))

    assert spec.pull() == cache_dir
    assert called is False


def test_repospec_cache_dir_uses_scheme_repo_key_and_digest(monkeypatch, tmp_path):
    git_spec = hub.RepoSpec.parse("git+https://example.com/repo.git")
    oras_spec = hub.RepoSpec.parse("oras://registry.example.com/models/model:v1")
    commit = "a" * 40
    digest = ORAS_DIGEST
    monkeypatch.setattr(hub.GitRepoSpec, "cache_digest", lambda self: commit)
    monkeypatch.setattr(hub.OrasRepoSpec, "_digest", lambda self: digest)
    monkeypatch.setattr(hub, "CACHE_ROOT", tmp_path / "cache")

    assert isinstance(git_spec, hub.GitRepoSpec)
    assert isinstance(oras_spec, hub.OrasRepoSpec)
    assert (
        git_spec.resolved().cache_dir()
        == tmp_path / "cache" / "git+https" / git_spec.repo_key() / commit
    )
    assert (
        oras_spec.resolved().cache_dir()
        == tmp_path
        / "cache"
        / "oras"
        / oras_spec.repo_key()
        / hub.OrasRepoSpec._digest_cache_name(digest)
    )


def test_pull_oras_pulls_when_missing(monkeypatch, tmp_path):
    spec = hub.RepoSpec.parse("oras://registry.example.com:5000/models/model:v1")
    digest = ORAS_DIGEST
    cache_root = tmp_path / "cache"
    assert isinstance(spec, hub.OrasRepoSpec)
    monkeypatch.setattr(hub.OrasRepoSpec, "_digest", lambda self: digest)
    monkeypatch.setattr(hub, "CACHE_ROOT", cache_root)

    digest_cache_name = spec._digest_cache_name(digest)
    repo_key = spec.repo_key()
    cache_dir_tmp = cache_root / "oras" / repo_key / f"tmp--{digest_cache_name}"
    calls: list[tuple[str, str | None, str | None]] = []

    class FakeRegistry:
        def pull(
            self,
            target,
            config_path=None,
            allowed_media_type=None,
            overwrite=True,
            outdir=None,
        ):
            calls.append((target, outdir, str(overwrite)))
            return []

    monkeypatch.setattr(
        hub.OrasRepoSpec, "_registry", staticmethod(lambda: FakeRegistry())
    )

    cache_dir = spec.pull()
    expected_dir = cache_root / "oras" / repo_key / digest_cache_name

    assert cache_dir == expected_dir
    assert expected_dir.exists()
    assert not cache_dir_tmp.exists()
    assert calls == [
        (
            f"registry.example.com:5000/models/model@{ORAS_DIGEST}",
            str(cache_dir_tmp),
            "True",
        )
    ]


def test_pull_oras_cleans_temp_dir_when_oras_exits(monkeypatch, tmp_path):
    spec = hub.RepoSpec.parse("oras://registry.example.com/models/model:v1")
    digest = ORAS_DIGEST
    cache_root = tmp_path / "cache"
    assert isinstance(spec, hub.OrasRepoSpec)
    monkeypatch.setattr(hub.OrasRepoSpec, "_digest", lambda self: digest)
    monkeypatch.setattr(hub, "CACHE_ROOT", cache_root)

    cache_dir_tmp = (
        cache_root
        / "oras"
        / spec.repo_key()
        / f"tmp--{hub.OrasRepoSpec._digest_cache_name(digest)}"
    )

    class FakeFileLock:
        def __init__(self, lock_path, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRegistry:
        def pull(
            self,
            target,
            config_path=None,
            allowed_media_type=None,
            overwrite=True,
            outdir=None,
        ):
            assert cache_dir_tmp.exists()
            raise SystemExit(2)

    monkeypatch.setattr(hub, "FileLock", FakeFileLock)
    monkeypatch.setattr(
        hub.OrasRepoSpec, "_registry", staticmethod(lambda: FakeRegistry())
    )

    with pytest.raises(SystemExit):
        spec.pull()

    assert not cache_dir_tmp.exists()


def test_pull_oras_reports_lock_timeout(monkeypatch, tmp_path):
    spec = hub.RepoSpec.parse("oras://registry.example.com/models/model:v1")
    digest = ORAS_DIGEST
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(hub.OrasRepoSpec, "_digest", lambda self: digest)
    monkeypatch.setattr(hub, "CACHE_ROOT", cache_root)

    class FakeFileLock:
        def __init__(self, lock_path, timeout):
            self.lock_path = lock_path

        def __enter__(self):
            raise hub.Timeout(str(self.lock_path))

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(hub, "FileLock", FakeFileLock)

    with pytest.raises(TimeoutError, match="Timed out waiting for cache lock"):
        spec.pull()


def test_repospec_cache_key_is_path_safe():
    spec = hub.RepoSpec.parse("oras://registry.example.com:5000/models/model:v1")
    assert isinstance(spec, hub.OrasRepoSpec)
    repo_key = spec.repo_key()

    assert repo_key == Path(repo_key).name
    assert os.sep not in repo_key
    if os.altsep is not None:
        assert os.altsep not in repo_key
    assert ":" not in repo_key
    assert all(c.isalnum() or c in {"-", "_", "."} for c in repo_key)
