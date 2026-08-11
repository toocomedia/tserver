"""Route all file actions to the verified root's storage adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from plugins.file_manager import file_service, host_file_service


def _service(context: file_service.FileContext):
    return host_file_service if context.root.kind == "host" else file_service


def list_entries(context: file_service.FileContext, path: str) -> dict[str, Any]:
    return _service(context).list_entries(context, path)


def read_text(context: file_service.FileContext, path: str) -> dict[str, Any]:
    return _service(context).read_text(context, path)


def write_text(context: file_service.FileContext, path: str, content: str, etag: str | None, protected: set[str], is_base64: bool = False) -> int:
    return _service(context).write_text(context, path, content, etag, protected, is_base64=is_base64)


def write_upload_file(context: file_service.FileContext, path: str, source: Path, size: int, etag: str | None) -> int:
    return _service(context).write_upload_file(context, path, source, size, etag)


def create_directory(context: file_service.FileContext, path: str) -> None:
    _service(context).create_directory(context, path)


def move_or_copy(context: file_service.FileContext, source: str, destination: str, *, copy: bool) -> None:
    _service(context).move_or_copy(context, source, destination, copy=copy)


def delete_path(context: file_service.FileContext, path: str) -> None:
    _service(context).delete_path(context, path)


def stage_download(context: file_service.FileContext, path: str) -> tuple[Path, int]:
    return _service(context).stage_download(context, path)
