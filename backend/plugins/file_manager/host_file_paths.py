"""Safe local filesystem primitives for panel-owned file roots."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile

from fastapi import HTTPException

from plugins.file_manager import file_service


def path_for(context: file_service.FileContext, relative_path: str, *, allow_root: bool = True) -> Path:
    relative_path = file_service.validate_relative_path(relative_path, allow_root=allow_root)
    root = Path(context.root.path)
    directory(root, "File root is unavailable.")
    target = root
    for part in relative_path.split("/") if relative_path else ():
        target /= part
        _reject_symlink(target)
    return target


def directory(path: Path, message: str = "Directory not found.") -> None:
    info = _stat(path)
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise HTTPException(404, message)


def file(path: Path, message: str = "File not found.") -> os.stat_result:
    info = _stat(path)
    if info is None or not stat.S_ISREG(info.st_mode):
        raise HTTPException(404, message)
    return info


def exists(path: Path) -> bool:
    return _stat(path) is not None


def etag(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def replace_bytes(path: Path, data: bytes) -> None:
    directory(path.parent)
    try:
        mode = path.stat().st_mode & 0o777
        if mode < 0o644:
            mode = 0o644
    except FileNotFoundError:
        mode = 0o644
        
    handle, temporary = tempfile.mkstemp(prefix=".srv-panel-file-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def replace_file(path: Path, source: Path) -> None:
    directory(path.parent)
    try:
        mode = path.stat().st_mode & 0o777
        if mode < 0o644:
            mode = 0o644
    except FileNotFoundError:
        mode = 0o644
        
    handle, temporary = tempfile.mkstemp(prefix=".srv-panel-file-", dir=path.parent)
    try:
        with source.open("rb") as input_file, os.fdopen(handle, "wb") as output:
            while block := input_file.read(1024 * 1024):
                output.write(block)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _stat(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise HTTPException(409, "Could not validate this filesystem path.") from exc
    if stat.S_ISLNK(info.st_mode):
        raise HTTPException(409, "Symlinks cannot be managed through File Manager.")
    return info


def _reject_symlink(path: Path) -> None:
    _stat(path)
