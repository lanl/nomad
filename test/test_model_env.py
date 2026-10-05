from __future__ import annotations

from hashlib import blake2b
from pathlib import Path

from nomad import model_env


def test_resolve_model_environment_uses_config_directory(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    requirements = config_dir / "requirements.txt"
    requirements.write_text("demo-package==1.2\n", encoding="utf-8")

    environment = model_env.resolve_model_environment(
        "requirements.txt", base_dir=config_dir
    )

    assert environment is not None
    assert environment.source == requirements
    assert environment.kind == "requirements.txt"
    assert (
        environment.checksum
        == blake2b(b"nomad-venv-requirements.txt-demo-package==1.2\n").hexdigest()
    )


def test_single_requirement_is_one_item_requirement_list(tmp_path: Path):
    single = model_env.resolve_model_environment("demo-package>=1", base_dir=tmp_path)
    listed = model_env.resolve_model_environment(["demo-package>=1"], base_dir=tmp_path)

    assert single is not None
    assert listed is not None
    assert single.requirements == ("demo-package>=1",)
    assert single.checksum == listed.checksum


def test_omitted_environment_uses_host_python(tmp_path: Path):
    environment = model_env.resolve_model_environment(None, base_dir=tmp_path)

    assert isinstance(environment, model_env.HostEnvironment)
    assert environment.ensure() == Path(model_env.sys.executable).absolute()


def test_pyproject_checksum_is_typed(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname='demo'\n", encoding="utf-8")

    environment = model_env.resolve_model_environment(pyproject.name, base_dir=tmp_path)

    assert environment is not None
    assert (
        environment.checksum
        == blake2b(b"nomad-venv-pyproject.toml-[project]\nname='demo'\n").hexdigest()
    )


def test_environment_cache_is_reused(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("NOMAD_CACHE", raising=False)
    monkeypatch.setattr(model_env.shutil, "which", lambda command: "/bin/uv")

    def fake_run(command: list[str], *, cwd: Path) -> None:
        commands.append(command)
        if command[1] == "venv":
            target = Path(command[-1])
            (target / "bin").mkdir(parents=True)
            (target / "bin" / "python").touch()

    monkeypatch.setattr(model_env, "_run", fake_run)
    environment = model_env.resolve_model_environment(
        ["demo-package"], base_dir=tmp_path
    )
    assert environment is not None

    first = environment.ensure()
    second = environment.ensure()

    assert first == second
    assert (
        first
        == tmp_path
        / "cache"
        / "nomad"
        / "venv"
        / environment.checksum
        / "bin"
        / "python"
    )
    assert len(commands) == 2
    assert commands[0][1:3] == ["venv", "--python"]
    assert "--system-site-packages" not in commands[0]
    assert commands[1][1:3] == ["pip", "install"]
    injected = (environment.cache_dir / ".nomad-requirements.txt").read_text()
    assert injected.splitlines()[0] == "demo-package"
    assert injected.splitlines()[1].startswith("-e file://")


def test_environment_creation_falls_back_to_stdlib_venv(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []
    monkeypatch.setenv("NOMAD_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(model_env.shutil, "which", lambda command: None)
    monkeypatch.setattr(
        model_env,
        "_nomad_requirement",
        lambda: ("nomad-scifm==1.2.3", "nomad-fingerprint"),
    )

    def fake_run(command: list[str], *, cwd: Path) -> None:
        commands.append(command)
        if command[1:3] == ["-m", "venv"]:
            target = Path(command[-1])
            (target / "bin").mkdir(parents=True)
            (target / "bin" / "python").touch()

    monkeypatch.setattr(model_env, "_run", fake_run)
    environment = model_env.resolve_model_environment([], base_dir=tmp_path)

    environment.ensure()

    assert commands[0][1:3] == ["-m", "venv"]
    assert commands[1][1:4] == ["-m", "pip", "install"]
    injected = (environment.cache_dir / ".nomad-requirements.txt").read_text()
    assert injected == "nomad-scifm==1.2.3\n"
