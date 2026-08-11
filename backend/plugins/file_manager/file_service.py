"""Confined file operations for active, panel-owned Apps Engine containers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import secrets
import shutil
import subprocess
import tempfile
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import config
from models.container_app import ContainerApp
from services import container_app_deployment_service, container_app_service


ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_locks: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class FileRoot:
    id: str
    label: str
    path: str
    kind: str
    persistent: bool
    sensitive: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.label,
            "label": self.label,
            "kind": self.kind,
            "persistence": "persistent" if self.persistent else "live_runtime",
            "edits_survive_deploy": self.persistent,
            "sensitive": self.sensitive,
        }


@dataclass(frozen=True)
class FileContext:
    app: Any
    container_name: str | None
    root: FileRoot


def lock_for(app_id: object) -> asyncio.Lock:
    return _locks.setdefault(app_id, asyncio.Lock())


async def resolve_context(db: AsyncSession, app_id: int, root_id: str) -> FileContext:
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")
    if app.status in {"deleting", "delete_failed", "data_preserved"}:
        raise HTTPException(409, "Finish deletion before accessing this app's files.")
    if await container_app_deployment_service.active_deployment(db, app.id):
        raise HTTPException(409, "File operations are unavailable while this app is deploying.")
    if app.status != "running":
        raise HTTPException(409, "Start this application before managing its files.")
    container = await asyncio.to_thread(_owned_container, app)
    roots = _roots(app, container)
    root = next((item for item in roots if item.id == root_id), None)
    if root is None:
        raise HTTPException(404, "File root is not available for this app.")
    return FileContext(app=app, container_name=app.container_name, root=root)


async def roots_for(db: AsyncSession, app_id: int) -> list[dict[str, Any]]:
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")
    if app.status != "running":
        raise HTTPException(409, "Start this application before managing its files.")
    container = await asyncio.to_thread(_owned_container, app)
    return [item.payload() for item in _roots(app, container)]


def validate_relative_path(value: str, *, allow_root: bool = True) -> str:
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        raise HTTPException(400, "Use a relative POSIX file path.")
    value = value.strip()
    if not value:
        if allow_root:
            return ""
        raise HTTPException(400, "A file or folder path is required.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(400, "Path must stay inside the selected file root.")
    normalized = path.as_posix()
    if len(normalized) > 1024:
        raise HTTPException(400, "Path is too long.")
    return normalized


def absolute_path(context: FileContext, relative_path: str) -> str:
    relative_path = validate_relative_path(relative_path)
    if context.root.kind == "environment":
        if relative_path != ".env":
            raise HTTPException(404, "Runtime environment exposes only .env.")
        return context.root.path
    return context.root.path if not relative_path else posixpath.join(context.root.path, relative_path)


def _owned_container(app: ContainerApp) -> dict[str, Any]:
    try:
        result = container_app_service._run(["docker", "inspect", app.container_name], timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(502, "Docker did not answer while verifying this application.") from exc
    if result.returncode:
        raise HTTPException(409, "The managed application container is unavailable.")
    try:
        values = json.loads(result.stdout)
        container = values[0]
        labels = container.get("Config", {}).get("Labels") or {}
    except (IndexError, TypeError, ValueError) as exc:
        raise HTTPException(502, "Could not verify the managed application container.") from exc
    if labels.get("srv-panel.plugin") != "railpack_apps" or labels.get("srv-panel.app-id") != str(app.id):
        raise HTTPException(409, "Container ownership verification failed.")
    if not container.get("State", {}).get("Running"):
        raise HTTPException(409, "The managed application container is not running.")
    return container


def _roots(app: ContainerApp, container: dict[str, Any]) -> list[FileRoot]:
    roots: list[FileRoot] = []
    if app.preset == "wordpress":
        roots.append(FileRoot("application", "WordPress files", "/var/www/html", "container", False))
    else:
        workdir = _safe_container_root(container.get("Config", {}).get("WorkingDir"))
        if workdir:
            roots.append(FileRoot("application", "Application files", workdir, "container", False))
    mounts = container.get("Mounts") or []
    data_path = _safe_container_root(app.data_mount_path)
    if app.data_volume and data_path and _has_volume(mounts, app.data_volume, data_path):
        roots.append(FileRoot("data", "Persistent data", data_path, "container", True))
    if app.wordpress_content_volume and _has_volume(mounts, app.wordpress_content_volume, "/var/www/html/wp-content"):
        roots.append(FileRoot("wordpress-content", "WordPress content", "/var/www/html/wp-content", "container", True))
    expected_env = str(container_app_service.env_path(app.id))
    if app.env_path == expected_env:
        roots.append(FileRoot("runtime-env", "Runtime .env", expected_env, "environment", True, True))
    return roots


def _safe_container_root(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        return None
    normalized = posixpath.normpath(value)
    if normalized in {"", ".", "/"} or not normalized.startswith("/"):
        return None
    return normalized


def _has_volume(mounts: list[dict[str, Any]], name: str, destination: str) -> bool:
    return any(
        item.get("Type") == "volume" and item.get("Name") == name
        and item.get("Destination") == destination
        for item in mounts if isinstance(item, dict)
    )


def _container_shell(context: FileContext, script: str, *args: str, timeout: int = 30):
    try:
        return container_app_service._run(
            ["docker", "exec", context.container_name, "sh", "-c", script, "file-manager", *args],
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(502, "Docker did not answer while accessing this file.") from exc


def _docker_error(result, fallback: str) -> HTTPException:
    detail = (result.stderr or result.stdout or fallback).strip()
    return HTTPException(502, detail[-1000:])


_NO_SYMLINKS = '''
path="$1"; root="$2"
while [ "$path" != "$root" ]; do
  [ -L "$path" ] && exit 0
  case "$path" in "$root"/*) ;; *) exit 3;; esac
  path=${path%/*}
done
[ -L "$root" ] && exit 0
exit 1
'''


def _require_no_symlinks(context: FileContext, path: str) -> None:
    result = _container_shell(context, _NO_SYMLINKS, path, context.root.path)
    if result.returncode == 1:
        return
    if result.returncode == 0:
        raise HTTPException(409, "Symlinks cannot be managed through File Manager.")
    raise _docker_error(result, "Could not validate the selected path.")


def _require_directory(context: FileContext, path: str) -> None:
    _require_no_symlinks(context, path)
    result = _container_shell(context, '[ -d "$1" ] && [ ! -L "$1" ]', path)
    if result.returncode:
        raise HTTPException(404, "Directory not found.")


def _file_exists(context: FileContext, path: str) -> bool:
    result = _container_shell(context, '[ -e "$1" ] || [ -L "$1" ]', path)
    return result.returncode == 0


def _file_size(context: FileContext, path: str) -> int:
    result = _container_shell(context, 'test -f "$1" && wc -c < "$1"', path)
    if result.returncode:
        raise HTTPException(404, "File not found.")
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise HTTPException(502, "Could not determine file size.") from exc


_LIST_DIRECTORY = r'''
dir="$1"; limit="$2"; count=0
[ -d "$dir" ] && [ ! -L "$dir" ] || exit 20
for item in "$dir"/..?* "$dir"/.[!.]* "$dir"/*; do
  [ -e "$item" ] || [ -L "$item" ] || continue
  name=${item##*/}; kind=file
  [ -d "$item" ] && kind=directory
  [ -L "$item" ] && kind=symlink
  size=0; [ "$kind" = file ] && size=$(wc -c < "$item" 2>/dev/null || printf 0)
  modified=$(stat -c %Y "$item" 2>/dev/null || stat -f %m "$item" 2>/dev/null || printf 0)
  printf '%s\000%s\000%s\000%s\000' "$name" "$kind" "$size" "$modified"
  count=$((count + 1)); [ "$count" -ge "$limit" ] && break
done
'''


def list_entries(context: FileContext, relative_path: str) -> dict[str, Any]:
    relative_path = validate_relative_path(relative_path)
    if context.root.kind == "environment":
        if relative_path:
            raise HTTPException(404, "Runtime environment exposes only .env.")
        path = Path(context.root.path)
        return {"path": "", "entries": ([{"name": ".env", "path": ".env", "kind": "file", "size": path.stat().st_size, "sensitive": True}] if path.is_file() else []), "has_more": False}
    path = absolute_path(context, relative_path)
    _require_directory(context, path)
    result = _container_shell(context, _LIST_DIRECTORY, path, str(config.FILE_MANAGER_MAX_ENTRIES))
    if result.returncode:
        raise _docker_error(result, "Could not list this directory.")
    values = result.stdout.split("\x00")
    if values and values[-1] == "":
        values.pop()
    if len(values) % 4:
        raise HTTPException(502, "Container returned an invalid directory listing.")
    entries = []
    for name, kind, size, modified in zip(values[::4], values[1::4], values[2::4], values[3::4]):
        child = name if not relative_path else f"{relative_path}/{name}"
        entries.append({"name": name, "path": child, "kind": kind, "size": _int_or_zero(size), "modified_at": _int_or_zero(modified), "sensitive": name == ".env"})
    return {"path": relative_path, "entries": entries, "has_more": len(entries) == config.FILE_MANAGER_MAX_ENTRIES}


def read_text(context: FileContext, relative_path: str) -> dict[str, Any]:
    data = _read_bytes(context, relative_path, config.FILE_MANAGER_MAX_TEXT_BYTES)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(415, "This file is not UTF-8 text.") from exc
    return {"path": validate_relative_path(relative_path, allow_root=False), "content": text, "etag": _etag(data), "size": len(data)}


def write_text(context: FileContext, relative_path: str, content: str, etag: str | None, protected_keys: set[str], is_base64: bool = False) -> int:
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
    return _write_bytes(context, relative_path, data, etag, protected_keys)


def write_upload(context: FileContext, relative_path: str, data: bytes, etag: str | None) -> int:
    if len(data) > config.FILE_MANAGER_MAX_TRANSFER_BYTES:
        raise HTTPException(413, "Uploads are limited to 100 MB. Use SFTP for larger files.")
    return _write_bytes(context, relative_path, data, etag, set())


def write_upload_file(context: FileContext, relative_path: str, source: Path, size: int, etag: str | None) -> int:
    """Streamed upload path; keeps the 100 MB transfer limit out of RAM."""
    if size > config.FILE_MANAGER_MAX_TRANSFER_BYTES:
        raise HTTPException(413, "Uploads are limited to 100 MB. Use SFTP for larger files.")
    relative_path = validate_relative_path(relative_path, allow_root=False)
    if context.root.kind == "environment":
        raise HTTPException(409, "Save Runtime .env with the text editor to preserve panel-managed values.")
    target = absolute_path(context, relative_path)
    parent = posixpath.dirname(target)
    _require_directory(context, parent)
    _require_no_symlinks(context, target)
    if _file_exists(context, target):
        if not etag:
            raise HTTPException(409, "Provide the current ETag before replacing an existing file.")
        if not secrets.compare_digest(_current_etag(context, relative_path), etag):
            raise HTTPException(409, "File changed on the server. Reload it before uploading.")
    _copy_file_to_container(context, target, source)
    return size


def create_directory(context: FileContext, relative_path: str) -> None:
    _require_container_root(context)
    relative_path = validate_relative_path(relative_path, allow_root=False)
    target = absolute_path(context, relative_path)
    parent = posixpath.dirname(target)
    _require_directory(context, parent)
    _require_no_symlinks(context, target)
    if _file_exists(context, target):
        raise HTTPException(409, "A file or folder already uses that name.")
    result = _container_shell(context, 'mkdir "$1"', target)
    if result.returncode:
        raise _docker_error(result, "Could not create the folder.")


def move_or_copy(context: FileContext, source_path: str, destination_path: str, *, copy: bool) -> None:
    _require_container_root(context)
    source = absolute_path(context, validate_relative_path(source_path, allow_root=False))
    destination = absolute_path(context, validate_relative_path(destination_path, allow_root=False))
    _require_no_symlinks(context, source)
    _require_directory(context, posixpath.dirname(destination))
    _require_no_symlinks(context, destination)
    if not _file_exists(context, source):
        raise HTTPException(404, "Source file or folder not found.")
    if _file_exists(context, destination):
        raise HTTPException(409, "Destination already exists.")
    command = 'cp -R "$1" "$2"' if copy else 'mv "$1" "$2"'
    result = _container_shell(context, command, source, destination, timeout=60)
    if result.returncode:
        raise _docker_error(result, "Could not copy the selected item." if copy else "Could not move the selected item.")


def delete_path(context: FileContext, relative_path: str) -> None:
    _require_container_root(context)
    target = absolute_path(context, validate_relative_path(relative_path, allow_root=False))
    _require_no_symlinks(context, target)
    if not _file_exists(context, target):
        raise HTTPException(404, "File or folder not found.")
    result = _container_shell(context, 'rm -rf "$1"', target, timeout=60)
    if result.returncode:
        raise _docker_error(result, "Could not delete the selected item.")


def stage_download(context: FileContext, relative_path: str) -> tuple[Path, int]:
    relative_path = validate_relative_path(relative_path, allow_root=False)
    if context.root.kind == "environment":
        path = Path(absolute_path(context, relative_path))
        if not path.is_file():
            raise HTTPException(404, "Runtime environment file is unavailable.")
        return _stage_local_file(path, config.FILE_MANAGER_MAX_TRANSFER_BYTES), path.stat().st_size
    absolute = absolute_path(context, relative_path)
    _require_no_symlinks(context, absolute)
    if _file_size(context, absolute) > config.FILE_MANAGER_MAX_TRANSFER_BYTES:
        raise HTTPException(413, "Downloads are limited to 100 MB. Use SFTP for larger files.")
    temporary = Path(tempfile.mkdtemp(prefix="srv-panel-file-download-"))
    try:
        result = container_app_service.run_binary(["docker", "cp", f"{context.container_name}:{absolute}", str(temporary)], timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise HTTPException(502, "Docker did not answer while preparing this download.") from exc
    target = temporary / PurePosixPath(absolute).name
    if result.returncode or not target.is_file() or target.is_symlink():
        shutil.rmtree(temporary, ignore_errors=True)
        if result.returncode:
            raise HTTPException(502, _binary_detail(result, "Could not prepare this download."))
        raise HTTPException(404, "File not found.")
    return target, target.stat().st_size


def cleanup_staged_file(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)


def _read_bytes(context: FileContext, relative_path: str, maximum: int) -> bytes:
    relative_path = validate_relative_path(relative_path, allow_root=False)
    if context.root.kind == "environment":
        path = Path(absolute_path(context, relative_path))
        if not path.is_file():
            raise HTTPException(404, "Runtime environment file is unavailable.")
        if path.stat().st_size > maximum:
            raise HTTPException(413, "Text files are limited to 2 MB.")
        return path.read_bytes()
    staged, size = stage_download(context, relative_path)
    try:
        if size > maximum:
            raise HTTPException(413, "Text files are limited to 2 MB.")
        return staged.read_bytes()
    finally:
        cleanup_staged_file(staged)


def _write_bytes(context: FileContext, relative_path: str, data: bytes, etag: str | None, protected_keys: set[str]) -> int:
    relative_path = validate_relative_path(relative_path, allow_root=False)
    if context.root.kind == "environment":
        if relative_path != ".env":
            raise HTTPException(404, "Runtime environment exposes only .env.")
        return _write_environment(context, data, etag, protected_keys)
    target = absolute_path(context, relative_path)
    parent = posixpath.dirname(target)
    _require_directory(context, parent)
    _require_no_symlinks(context, target)
    if _file_exists(context, target):
        if not etag:
            raise HTTPException(409, "Provide the current ETag before replacing an existing file.")
        current = _read_bytes(context, relative_path, config.FILE_MANAGER_MAX_TEXT_BYTES)
        if not secrets.compare_digest(_etag(current), etag):
            raise HTTPException(409, "File changed on the server. Reload it before saving.")
    _copy_bytes_to_container(context, target, data)
    return len(data)


def _copy_bytes_to_container(context: FileContext, target: str, data: bytes) -> None:
    parent = posixpath.dirname(target)
    name = f".srv-panel-upload-{secrets.token_hex(12)}"
    with tempfile.TemporaryDirectory(prefix="srv-panel-file-upload-") as directory:
        source = Path(directory) / name
        source.write_bytes(data)
        _copy_file_to_container(context, target, source, staging_name=name)


def _copy_file_to_container(context: FileContext, target: str, source: Path, *, staging_name: str | None = None) -> None:
    name = staging_name or f".srv-panel-upload-{secrets.token_hex(12)}"
    staged_source = source
    if source.name != name:
        staging_dir = Path(tempfile.mkdtemp(prefix="srv-panel-file-stage-"))
        staged_source = staging_dir / name
        shutil.copyfile(source, staged_source)
    else:
        staging_dir = None
    try:
        try:
            result = container_app_service.run_binary(["docker", "cp", str(staged_source), f"{context.container_name}:{target}"], timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(502, "Docker did not answer while uploading this file.") from exc
        if result.returncode:
            raise HTTPException(502, _binary_detail(result, "Could not upload this file."))
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)


def _current_etag(context: FileContext, relative_path: str) -> str:
    staged, _ = stage_download(context, relative_path)
    try:
        digest = hashlib.sha256()
        with staged.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    finally:
        cleanup_staged_file(staged)


def _write_environment(context: FileContext, data: bytes, etag: str | None, protected_keys: set[str]) -> int:
    path = Path(context.root.path)
    current = path.read_bytes() if path.is_file() else b""
    if not etag or not secrets.compare_digest(_etag(current), etag):
        raise HTTPException(409, "Runtime environment changed on the server. Reload it before saving.")
    try:
        proposed = _parse_environment(data.decode("utf-8"))
        existing = _parse_environment(current.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "Runtime environment must be UTF-8 text.") from exc
    for key in protected_keys:
        if key in existing and proposed.get(key) != existing[key]:
            raise HTTPException(409, f"{key} is managed by the panel and cannot be changed here.")
    container_app_service.write_env(path, proposed)
    return len(data)


def _parse_environment(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        if not line:
            continue
        if "=" not in line:
            raise HTTPException(400, "Runtime environment lines must use NAME=value.")
        key, value = line.split("=", 1)
        if not ENV_KEY_RE.fullmatch(key):
            raise HTTPException(400, "Runtime environment contains an invalid variable name.")
        values[key] = value
    return values


def _require_container_root(context: FileContext) -> None:
    if context.root.kind == "environment":
        raise HTTPException(409, "Runtime .env supports only reading, saving, and downloading.")


def _stage_local_file(source: Path, maximum: int) -> Path:
    if source.stat().st_size > maximum:
        raise HTTPException(413, "Downloads are limited to 100 MB. Use SFTP for larger files.")
    directory = Path(tempfile.mkdtemp(prefix="srv-panel-file-download-"))
    target = directory / source.name
    shutil.copyfile(source, target)
    return target


def _etag(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _int_or_zero(value: str) -> int:
    try:
        return int(float(value))
    except ValueError:
        return 0


def _binary_detail(result, fallback: str) -> str:
    output = result.stderr or result.stdout or b""
    return output.decode(errors="replace").strip()[-1000:] or fallback
