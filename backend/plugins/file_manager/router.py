"""JSON and file-transfer API for the hidden File Manager plugin."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
from typing import Any, Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from database import get_db
import config
from models.container_app import ContainerApp
from plugins.file_manager import audit, file_operations, file_service, file_targets
from services import container_app_database_service
from templating import templates


router = APIRouter(prefix="/plugins/file_manager", tags=["file-manager"])


@router.get("/", response_class=HTMLResponse)
async def file_manager_index(request: Request):
    return templates.TemplateResponse("file_manager.html", {
        "request": request,
        "active_page": "plugins"
    })


class TextWrite(BaseModel):
    path: str
    content: str
    etag: str | None = None


class DirectoryCreate(BaseModel):
    path: str


class Transfer(BaseModel):
    source_path: str
    destination_path: str


class DeleteRequest(BaseModel):
    path: str
    confirmation: str


@router.get("/api/apps")
async def apps(db: AsyncSession = Depends(get_db)):
    return {"apps": await file_targets.list_targets(db)}


@router.get("/api/apps/{app_id}/roots")
async def roots(app_id: str, db: AsyncSession = Depends(get_db)):
    target = file_targets.parse_target(app_id)
    return {"app_id": target.id, "roots": await file_targets.roots_for(db, target)}


@router.get("/api/apps/{app_id}/roots/{root_id}/entries")
async def entries(
    app_id: str, root_id: str, request: Request, path: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    return await _operation(request, db, app_id, root_id, path, "list", lambda context: file_operations.list_entries(context, path))


@router.get("/api/apps/{app_id}/roots/{root_id}/text")
async def read_text(
    app_id: str, root_id: str, request: Request, path: str,
    db: AsyncSession = Depends(get_db),
):
    return await _operation(request, db, app_id, root_id, path, "read", lambda context: file_operations.read_text(context, path))


@router.post("/api/apps/{app_id}/roots/{root_id}/text")
async def write_text(
    app_id: str, root_id: str, body: TextWrite, request: Request,
    db: AsyncSession = Depends(get_db),
):
    protected = await _protected_environment_keys(db, app_id, root_id)
    return await _operation(request, db, app_id, root_id, body.path, "write", lambda context: _write(context, body, protected))


@router.post("/api/apps/{app_id}/roots/{root_id}/directories")
async def create_directory(
    app_id: str, root_id: str, body: DirectoryCreate, request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _operation(request, db, app_id, root_id, body.path, "mkdir", lambda context: _mkdir(context, body.path))


@router.post("/api/apps/{app_id}/roots/{root_id}/upload")
async def upload(
    app_id: str, root_id: str, request: Request, path: str = Form(...),
    etag: str | None = Form(None), file: UploadFile = File(...), db: AsyncSession = Depends(get_db),
):
    temporary, size = await _receive_upload(file)
    try:
        return await _operation(request, db, app_id, root_id, path, "upload", lambda context: {
            "size": file_operations.write_upload_file(context, path, temporary, size, etag)
        }, size_bytes=size)
    finally:
        temporary.unlink(missing_ok=True)


@router.get("/api/apps/{app_id}/roots/{root_id}/download")
async def download(
    app_id: str, root_id: str, request: Request, path: str,
    db: AsyncSession = Depends(get_db),
):
    staged, size = await _operation(request, db, app_id, root_id, path, "download", lambda context: file_operations.stage_download(context, path))
    return FileResponse(
        staged, media_type="application/octet-stream", filename=staged.name,
        background=BackgroundTask(file_service.cleanup_staged_file, staged),
        headers={"X-File-Size": str(size)},
    )


@router.post("/api/apps/{app_id}/roots/{root_id}/move")
async def move(
    app_id: str, root_id: str, body: Transfer, request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _operation(request, db, app_id, root_id, body.source_path, "move", lambda context: _transfer(context, body, copy=False))


@router.post("/api/apps/{app_id}/roots/{root_id}/copy")
async def copy(
    app_id: str, root_id: str, body: Transfer, request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _operation(request, db, app_id, root_id, body.source_path, "copy", lambda context: _transfer(context, body, copy=True))


@router.delete("/api/apps/{app_id}/roots/{root_id}/entries")
async def delete(
    app_id: str, root_id: str, body: DeleteRequest, request: Request,
    db: AsyncSession = Depends(get_db),
):
    path = file_service.validate_relative_path(body.path, allow_root=False)
    if body.confirmation != f"DELETE {path}":
        raise HTTPException(400, f"Type DELETE {path} to permanently delete this item.")
    return await _operation(request, db, app_id, root_id, path, "delete", lambda context: _delete(context, path))


async def _operation(
    request: Request, db: AsyncSession, app_id: str, root_id: str, path: str,
    action: str, operation: Callable[[file_service.FileContext], Any], *, size_bytes: int | None = None,
):
    target = file_targets.parse_target(app_id)
    safe_path = _audit_path(path)
    async with file_service.lock_for(target.id):
        try:
            context = await file_targets.resolve_context(db, target, root_id)
            result = await asyncio.to_thread(operation, context)
        except HTTPException:
            await audit.record(db, request, app_id=target.resource_id, target_type=target.kind, root_id=root_id, relative_path=safe_path, action=action, result="failed", size_bytes=size_bytes)
            await db.commit()
            raise
    count = len(result.get("entries", [])) if isinstance(result, dict) and isinstance(result.get("entries"), list) else None
    recorded_size = result.get("size", size_bytes) if isinstance(result, dict) else size_bytes
    await audit.record(db, request, app_id=target.resource_id, target_type=target.kind, root_id=root_id, relative_path=safe_path, action=action, result="success", size_bytes=recorded_size, item_count=count)
    return result if result is not None else {"ok": True}


async def _protected_environment_keys(db: AsyncSession, app_id: str, root_id: str) -> set[str]:
    target = file_targets.parse_target(app_id)
    if target.kind != "container" or root_id != "runtime-env":
        return set()
    app = await db.get(ContainerApp, target.resource_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")
    attachments = await container_app_database_service.attachments_for(db, app.id)
    return {"PORT", *(item.environment_key for item in attachments if item.environment_key)}


async def _receive_upload(file: UploadFile) -> tuple[Path, int]:
    suffix = Path(file.filename or "upload").suffix[:32]
    handle = tempfile.NamedTemporaryFile(prefix="srv-panel-upload-", suffix=suffix, delete=False)
    path = Path(handle.name)
    size = 0
    try:
        while block := await file.read(1024 * 1024):
            size += len(block)
            if size > config.FILE_MANAGER_MAX_TRANSFER_BYTES:
                raise HTTPException(413, "Uploads are limited to 100 MB. Use SFTP for larger files.")
            handle.write(block)
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    handle.close()
    return path, size


def _mkdir(context: file_service.FileContext, path: str) -> dict[str, bool]:
    file_operations.create_directory(context, path)
    return {"ok": True}


def _write(context: file_service.FileContext, body: TextWrite, protected: set[str]) -> dict[str, Any]:
    return {
        "size": file_operations.write_text(context, body.path, body.content, body.etag, protected),
        "restart_required": context.root.kind == "environment",
    }


def _transfer(context: file_service.FileContext, body: Transfer, *, copy: bool) -> dict[str, bool]:
    file_operations.move_or_copy(context, body.source_path, body.destination_path, copy=copy)
    return {"ok": True}


def _delete(context: file_service.FileContext, path: str) -> dict[str, bool]:
    file_operations.delete_path(context, path)
    return {"ok": True}


def _audit_path(path: str) -> str:
    try:
        return file_service.validate_relative_path(path)
    except HTTPException:
        return ""
