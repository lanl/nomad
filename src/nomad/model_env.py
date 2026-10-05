from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from hashlib import blake2b
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

from .hub import get_cache_root

logger = logging.getLogger(__name__)

_COMPLETE_MARKER = ".nomad-complete"
_CACHE_FORMAT = "2"
_HASH_PREFIX = "nomad-venv-"


@dataclass(frozen=True, slots=True)
class ModelEnvironment:
    """A normalized model environment and its content-addressed cache key."""

    kind: str
    contents: bytes
    requirements: tuple[str, ...] = ()
    source: Path | None = None
    base_dir: Path = field(default_factory=Path.cwd)

    @property
    def checksum(self) -> str:
        digest = blake2b()
        digest.update(f"{_HASH_PREFIX}{self.kind}-".encode())
        digest.update(self.contents)
        return digest.hexdigest()

    @property
    def cache_dir(self) -> Path:
        return get_cache_root() / "venv" / self.checksum

    @property
    def python(self) -> Path:
        return _venv_python(self.cache_dir)

    def ensure(self) -> Path:
        """Create the cached environment if necessary and return its Python."""
        root = get_cache_root() / "venv"
        root.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir
        lock = FileLock(str(root / f".{self.checksum}.lock"))
        with lock:
            python = _venv_python(target)
            marker = target / _COMPLETE_MARKER
            _, nomad_fingerprint = _nomad_requirement()
            expected_marker = f"{_CACHE_FORMAT}:{self.checksum}:{nomad_fingerprint}\n"
            if (
                python.is_file()
                and marker.is_file()
                and marker.read_text(encoding="utf-8") == expected_marker
            ):
                logger.debug("Reusing model environment %s", target)
                return python

            if target.exists():
                shutil.rmtree(target)

            temporary = root / f".{self.checksum}.tmp-{uuid4().hex}"
            try:
                self._create(temporary)
                (temporary / _COMPLETE_MARKER).write_text(
                    expected_marker, encoding="utf-8"
                )
                temporary.replace(target)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise

        return _venv_python(target)

    def _create(self, target: Path) -> None:
        uv = shutil.which("uv")
        if uv:
            logger.info("Creating model environment %s with uv", self.checksum)
            _run(
                [
                    uv,
                    "venv",
                    "--python",
                    sys.executable,
                    str(target),
                ],
                cwd=self.base_dir,
            )
            installer = [uv, "pip", "install", "--python", str(_venv_python(target))]
        else:
            logger.info(
                "uv is unavailable; creating model environment %s with venv",
                self.checksum,
            )
            _run(
                [sys.executable, "-m", "venv", str(target)],
                cwd=self.base_dir,
            )
            installer = [str(_venv_python(target)), "-m", "pip", "install"]

        requirements = target / ".nomad-requirements.txt"
        requirements.write_text(
            "\n".join([*self._requirement_lines(), _nomad_requirement()[0]]) + "\n",
            encoding="utf-8",
        )
        _run([*installer, "-r", str(requirements)], cwd=self.base_dir)

    def _requirement_lines(self) -> list[str]:
        if self.kind == "requirements.txt" and self.source is not None:
            return [f"-r {self.source.as_uri()}"]
        if self.kind == "pyproject.toml":
            assert self.source is not None
            return [self.source.parent.as_uri()]
        return list(self.requirements)


@dataclass(frozen=True, slots=True)
class HostEnvironment:
    """The Python environment already running the Nomad host process."""

    python: Path
    prefix: Path

    @property
    def checksum(self) -> str:
        return blake2b(f"{_HASH_PREFIX}host-{self.prefix}".encode()).hexdigest()

    def ensure(self) -> Path:
        return self.python


PythonEnvironment = ModelEnvironment | HostEnvironment


def resolve_model_environment(
    value: str | list[str] | None,
    *,
    base_dir: Path | None = None,
) -> PythonEnvironment:
    """Resolve a model ``env`` value relative to its configuration file."""
    if value is None:
        return HostEnvironment(
            python=Path(sys.executable).absolute(),
            prefix=Path(sys.prefix).resolve(),
        )

    resolved_base = (base_dir or Path.cwd()).expanduser().resolve()
    if isinstance(value, str):
        candidate = (resolved_base / Path(value).expanduser()).resolve()
        if candidate.name in {"requirements.txt", "pyproject.toml"}:
            if not candidate.is_file():
                raise FileNotFoundError(
                    f"Model environment file not found: {candidate}"
                )
            return ModelEnvironment(
                kind=candidate.name,
                contents=candidate.read_bytes(),
                source=candidate,
                base_dir=resolved_base,
            )
        requirements = (value,)
    else:
        requirements = tuple(value)

    contents = "\n".join(requirements).encode()
    return ModelEnvironment(
        kind="requirements.txt",
        contents=contents,
        requirements=requirements,
        base_dir=resolved_base,
    )


def _venv_python(venv: Path) -> Path:
    posix_python = venv / "bin" / "python"
    if posix_python.exists() or not (venv / "Scripts" / "python.exe").exists():
        return posix_python
    return venv / "Scripts" / "python.exe"


def _nomad_requirement() -> tuple[str, str]:
    """Return a requirement for the running Nomad and its cache fingerprint."""
    source_root = Path(__file__).resolve().parent.parent.parent
    pyproject = source_root / "pyproject.toml"
    if pyproject.is_file():
        fingerprint = blake2b(pyproject.read_bytes()).hexdigest()
        return f"-e {source_root.as_uri()}", fingerprint

    try:
        installed_version = version("nomad-scifm")
    except PackageNotFoundError as exc:  # pragma: no cover - broken installation
        raise RuntimeError(
            "Cannot construct a model environment because Nomad is not installed"
        ) from exc
    requirement = f"nomad-scifm=={installed_version}"
    return requirement, blake2b(requirement.encode()).hexdigest()


def _run(command: list[str], *, cwd: Path) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to construct model environment with {Path(command[0]).name}"
        ) from exc


__all__ = [
    "HostEnvironment",
    "ModelEnvironment",
    "PythonEnvironment",
    "resolve_model_environment",
]
