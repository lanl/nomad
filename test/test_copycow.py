from __future__ import annotations

from pathlib import Path

import pytest

from nomad.copycow import copy_cow


def test_copy_cow_copies_file(tmp_path: Path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("hello\n", encoding="utf-8")

    copy_cow(src, dst)

    assert dst.read_text("utf-8") == "hello\n"


def test_copy_cow_recursively_copies_directories(tmp_path: Path):
    src = tmp_path / "src"
    nested = src / "nested"
    nested.mkdir(parents=True)
    (src / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (nested / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    dst = tmp_path / "dst"
    copy_cow(src, dst)

    assert (dst / "__init__.py").read_text("utf-8") == "VALUE = 1\n"
    assert (dst / "nested" / "module.py").read_text("utf-8") == "VALUE = 2\n"


def test_copy_cow_raises_if_directory_destination_exists(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "fresh.py").write_text("fresh = True\n", encoding="utf-8")

    dst = tmp_path / "dst"
    dst.mkdir()

    with pytest.raises(FileExistsError, match="Destination already exists"):
        copy_cow(src, dst)


def test_copy_cow_resolves_directory_symlinks_by_default(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    real_dir = source / "real"
    real_dir.mkdir()
    (real_dir / "payload.txt").write_text("payload\n", encoding="utf-8")
    (source / "linked").symlink_to("real", target_is_directory=True)

    destination = tmp_path / "destination"
    copy_cow(source, destination)

    copied = destination / "linked" / "payload.txt"
    assert copied.read_text("utf-8") == "payload\n"
    assert (destination / "linked").is_symlink() is False


def test_copy_cow_preserves_directory_symlinks_when_disabled(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    real_dir = source / "real"
    real_dir.mkdir()
    (real_dir / "payload.txt").write_text("payload\n", encoding="utf-8")
    (source / "linked").symlink_to("real", target_is_directory=True)

    destination = tmp_path / "destination"
    copy_cow(source, destination, symlinks=False)

    copied = destination / "linked"
    assert copied.is_symlink() is True
    assert (copied / "payload.txt").read_text("utf-8") == "payload\n"
