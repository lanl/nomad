from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

PathLike = str | Path


def copy_cow(src: PathLike, dst: PathLike, *, symlinks: bool = True) -> None:
    """
    Copy a file or directory from src -> dst, preferring copy-on-write where
    available by shelling out to the system 'cp' command for files. Directory
    copies are delegated to `shutil.copytree(..., copy_function=copy_cow)`.

    When `symlinks` is True, symlinked files are resolved before copying.
    When `symlinks` is False, symlinks are preserved during directory copies.

    Requires dst to not exist.
    Raises FileNotFoundError if src doesn't exist.
    May raise subprocess.CalledProcessError on unexpected cp failures.
    """
    src = Path(src)
    dst = Path(dst)

    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src}")

    if dst.exists():
        raise FileExistsError(f"Destination already exists: {dst}")

    if src.is_dir():
        shutil.copytree(
            src,
            dst,
            symlinks=not symlinks,
            copy_function=partial(copy_cow, symlinks=symlinks),
        )
        return

    if symlinks:
        src = src.resolve()

    if not src.is_file():
        raise FileNotFoundError(f"Source not found or not a file: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "darwin":
        subprocess.run(["cp", "-c", str(src), str(dst)], check=True)
        return

    system = platform.system()

    if system == "Linux":
        subprocess.run(["cp", "--reflink=auto", str(src), str(dst)], check=True)
        return

    shutil.copy2(str(src), str(dst))
