"""File operations for verified local static and Python application roots."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import stat
import tempfile
from typing import Any

from fastapi import HTTPException

import config
from plugins.file_manager import file_service
from plugins.file_manager import host_file_paths as paths


def list_entries(context: file_service.FileContext, relative_path: str) -> dict[str, Any]:
    relative_path = file_service.validate_relative_path(relative_path)
    folder = paths.path_for(context, relative_path)
    paths.directory(folder)
    entries = []
    for child in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if len(entries) >= config.FILE_MANAGER_MAX_ENTRIES:
            break
        info = child.lstat()
        kind = "symlink" if stat.S_ISLNK(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode) else "file"
        child_path = child.name if not relative_path else f"{relative_path}/{child.name}"
        entries.append({
            "name": child.name, "path": child_path, "kind": kind,
            "size": info.st_size if kind == "file" else 0,
            "modified_at": int(info.st_mtime), "sensitive": child.name == ".env",
        })
    return {"path": relative_path, "entries": entries, "has_more": len(entries) == config.FILE_MANAGER_MAX_ENTRIES}


def read_text(context: file_service.FileContext, relative_path: str) -> dict[str, Any]:
    source = paths.path_for(context, relative_path, allow_root=False)
    info = paths.file(source)
    if info.st_size > config.FILE_MANAGER_MAX_TEXT_BYTES:
        raise HTTPException(413, "Text files are limited to 2 MB. Use SFTP for larger files.")
    data = source.read_bytes()
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(415, "This file is not UTF-8 text.") from exc
    return {"path": relative_path, "content": content, "etag": paths.etag(source), "size": len(data)}


def write_text(context: file_service.FileContext, relative_path: str, content: str, etag: str | None, _protected: set[str], is_base64: bool = False) -> int:
    if not isinstance(content, str):
        raise HTTPException(400, "Text content is required.")
    
    if is_base64:
        import base64
        try:
            data = base64.b64decode(content)
        except Exception:
            raise HTTPException(400, "Invalid base64 payload.")
    else:
        data = content.encode("utf-8")
        
    if len(data) > config.FILE_MANAGER_MAX_TEXT_BYTES:
        raise HTTPException(413, "Text files are limited to 2 MB. Use SFTP for larger files.")
    return _write(context, relative_path, data, etag)


def write_upload(context: file_service.FileContext, relative_path: str, data: bytes, etag: str | None) -> int:
    if len(data) > config.FILE_MANAGER_MAX_TRANSFER_BYTES:
        raise HTTPException(413, "Uploads are limited to 100 MB. Use SFTP for larger files.")
    return _write(context, relative_path, data, etag)


def write_upload_file(context: file_service.FileContext, relative_path: str, source: Path, size: int, etag: str | None) -> int:
    if size > config.FILE_MANAGER_MAX_TRANSFER_BYTES:
        raise HTTPException(413, "Uploads are limited to 100 MB. Use SFTP for larger files.")
    target = _write_target(context, relative_path, etag)
    paths.replace_file(target, source)
    return size


def create_directory(context: file_service.FileContext, relative_path: str) -> None:
    target = paths.path_for(context, relative_path, allow_root=False)
    paths.directory(target.parent)
    if paths.exists(target):
        raise HTTPException(409, "A file or folder already uses that name.")
    target.mkdir()


def move_or_copy(context: file_service.FileContext, source_path: str, destination_path: str, *, copy: bool) -> None:
    source = paths.path_for(context, source_path, allow_root=False)
    destination = paths.path_for(context, destination_path, allow_root=False)
    if not paths.exists(source):
        raise HTTPException(404, "Source file or folder not found.")
    paths.directory(destination.parent)
    if paths.exists(destination):
        raise HTTPException(409, "Destination already exists.")
    _require_tree_without_symlinks(source)
    if copy:
        shutil.copytree(source, destination) if source.is_dir() else shutil.copyfile(source, destination)
    else:
        source.replace(destination)


def delete_path(context: file_service.FileContext, relative_path: str) -> None:
    target = paths.path_for(context, relative_path, allow_root=False)
    if not paths.exists(target):
        raise HTTPException(404, "File or folder not found.")
    _require_tree_without_symlinks(target)
    shutil.rmtree(target) if target.is_dir() else target.unlink()


def stage_download(context: file_service.FileContext, relative_path: str) -> tuple[Path, int]:
    source = paths.path_for(context, relative_path, allow_root=False)
    info = paths.file(source)
    if info.st_size > config.FILE_MANAGER_MAX_TRANSFER_BYTES:
        raise HTTPException(413, "Downloads are limited to 100 MB. Use SFTP for larger files.")
    directory = Path(tempfile.mkdtemp(prefix="srv-panel-file-download-"))
    target = directory / source.name
    shutil.copyfile(source, target)
    return target, info.st_size


def _write(context: file_service.FileContext, relative_path: str, data: bytes, etag: str | None) -> int:
    target = _write_target(context, relative_path, etag)
    paths.replace_bytes(target, data)
    return len(data)


def _write_target(context: file_service.FileContext, relative_path: str, etag: str | None) -> Path:
    target = paths.path_for(context, relative_path, allow_root=False)
    paths.directory(target.parent)
    if paths.exists(target):
        paths.file(target)
        if not etag:
            raise HTTPException(409, "Provide the current ETag before replacing an existing file.")
        if not secrets.compare_digest(paths.etag(target), etag):
            raise HTTPException(409, "File changed on the server. Reload it before saving.")
    return target


def _require_tree_without_symlinks(source: Path) -> None:
    if not source.is_dir():
        return
    for parent, directories, files in os.walk(source, followlinks=False):
        for name in [*directories, *files]:
            if stat.S_ISLNK((Path(parent) / name).lstat().st_mode):
                raise HTTPException(409, "Folders containing symlinks cannot be managed through File Manager.")
