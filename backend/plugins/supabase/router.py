from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from templating import templates
import plugins.supabase.service as svc
from plugins.supabase.schemas import (
    ProjectCreate,
    ProjectUpdate,
    QueryRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugins/supabase", tags=["supabase"])


# ──────────────────────────────────────────────
# Connect wizard API
# ──────────────────────────────────────────────

class _FetchProjectsRequest(BaseModel):
    pat: str

class _ImportEntry(BaseModel):
    ref: str
    name: str
    db_host: str
    db_port: int = 5432
    db_name: str = "postgres"
    db_user: str = "postgres"
    region: str | None = None
    db_password: str

class _ImportRequest(BaseModel):
    pat: str
    projects: list[_ImportEntry]


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def supabase_index(request: Request, db: AsyncSession = Depends(get_db)):
    projects = await svc.list_projects(db)
    return templates.TemplateResponse(
        "supabase/index.html",
        {"request": request, "projects": projects},
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def supabase_project_detail(
    request: Request, project_id: int, db: AsyncSession = Depends(get_db)
):
    project = await svc.get_project(db, project_id)
    databases = []
    database_error = None
    try:
        databases = await svc.list_databases(project_id, db)
    except HTTPException as exc:
        database_error = exc.detail
    except Exception:
        logger.exception("Could not load databases for Supabase project %s", project_id)
        database_error = "Could not load the Supabase database list."
    return templates.TemplateResponse(
        "supabase/project_detail.html",
        {
            "request": request,
            "project": project,
            "databases": databases,
            "database_error": database_error,
        },
    )


# ──────────────────────────────────────────────
# API — Projects
# ──────────────────────────────────────────────

@router.get("/api/projects")
async def api_list_projects(db: AsyncSession = Depends(get_db)):
    projects = await svc.list_projects(db)
    return [
        {
            "id": p.id,
            "name": p.name,
            "db_host": p.db_host,
            "region": p.region,
            "connection_status": p.connection_status,
            "last_connected_at": p.last_connected_at.isoformat() if p.last_connected_at else None,
        }
        for p in projects
    ]


@router.post("/api/projects", status_code=201)
async def api_create_project(payload: ProjectCreate, db: AsyncSession = Depends(get_db)):
    proj = await svc.create_project(
        db,
        name=payload.name,
        db_host=payload.db_host,
        db_port=payload.db_port,
        db_name=payload.db_name,
        db_user=payload.db_user,
        db_password=payload.db_password,
        pat=payload.pat,
        region=payload.region,
    )
    # Test connection right away
    result = await svc.test_connection(db, proj.id)
    return {
        "id": proj.id,
        "name": proj.name,
        "connection_status": proj.connection_status,
        "test": result,
    }


@router.get("/api/projects/{project_id}")
async def api_get_project(project_id: int, db: AsyncSession = Depends(get_db)):
    proj = await svc.get_project(db, project_id)
    return {
        "id": proj.id,
        "name": proj.name,
        "project_ref": proj.project_ref,
        "db_host": proj.db_host,
        "db_port": proj.db_port,
        "db_name": proj.db_name,
        "db_user": proj.db_user,
        "region": proj.region,
        "connection_status": proj.connection_status,
        "last_connected_at": proj.last_connected_at.isoformat() if proj.last_connected_at else None,
        "created_at": proj.created_at.isoformat(),
    }


@router.patch("/api/projects/{project_id}")
async def api_update_project(
    project_id: int, payload: ProjectUpdate, db: AsyncSession = Depends(get_db)
):
    proj = await svc.update_project(
        db,
        project_id,
        name=payload.name,
        db_password=payload.db_password,
        pat=payload.pat,
        region=payload.region,
    )
    return {"id": proj.id, "name": proj.name}


@router.delete("/api/projects/{project_id}", status_code=204)
async def api_delete_project(project_id: int, db: AsyncSession = Depends(get_db)):
    await svc.delete_project(db, project_id)


@router.post("/api/projects/{project_id}/test")
async def api_test_connection(project_id: int, db: AsyncSession = Depends(get_db)):
    return await svc.test_connection(db, project_id)


# ──────────────────────────────────────────────
# API — Database browser
# ──────────────────────────────────────────────

@router.get("/api/projects/{project_id}/databases")
async def api_list_databases(project_id: int, db: AsyncSession = Depends(get_db)):
    return await svc.list_databases(project_id, db)


@router.get("/api/projects/{project_id}/databases/{database}/tables")
async def api_list_tables(
    project_id: int, database: str, db: AsyncSession = Depends(get_db)
):
    return await svc.list_tables(project_id, database, db)


@router.get("/api/projects/{project_id}/roles")
async def api_list_roles(project_id: int, db: AsyncSession = Depends(get_db)):
    return await svc.list_roles(project_id, db)


# ──────────────────────────────────────────────
# API — Query runner
# ──────────────────────────────────────────────

@router.post("/api/query")
async def api_run_query(payload: QueryRequest, db: AsyncSession = Depends(get_db)):
    return await svc.run_query(payload.project_id, payload.db, payload.sql, db)


# ──────────────────────────────────────────────
# API — Management API (PAT required)
# ──────────────────────────────────────────────

@router.get("/api/projects/{project_id}/stats")
async def api_project_stats(project_id: int, db: AsyncSession = Depends(get_db)):
    return await svc.get_project_stats(project_id, db)


@router.post("/api/projects/{project_id}/pause")
async def api_pause_project(project_id: int, db: AsyncSession = Depends(get_db)):
    return await svc.pause_project(project_id, db)


@router.post("/api/projects/{project_id}/restore")
async def api_restore_project(project_id: int, db: AsyncSession = Depends(get_db)):
    return await svc.restore_project(project_id, db)


# ──────────────────────────────────────────────
# API — Connect wizard (PAT → fetch → import)
# ──────────────────────────────────────────────

@router.post("/api/connect/fetch-projects")
async def api_connect_fetch(payload: _FetchProjectsRequest):
    """Step 1: validate PAT and return all Supabase projects in the account."""
    return await svc.fetch_account_projects(payload.pat)


@router.post("/api/connect/import", status_code=201)
async def api_connect_import(payload: _ImportRequest, db: AsyncSession = Depends(get_db)):
    """Step 2: save selected projects (with passwords) into panel DB."""
    saved = []
    for entry in payload.projects:
        # Skip if already imported (same project_ref)
        from sqlalchemy import select
        from models.supabase_project import SupabaseProject
        existing = await db.scalar(
            select(SupabaseProject).where(SupabaseProject.project_ref == entry.ref)
        )
        if existing:
            saved.append({"id": existing.id, "name": existing.name, "skipped": True})
            continue
        proj = await svc.create_project(
            db,
            name=entry.name,
            db_host=entry.db_host,
            db_port=entry.db_port,
            db_name=entry.db_name,
            db_user=entry.db_user,
            db_password=entry.db_password,
            pat=payload.pat,
            region=entry.region,
        )
        result = await svc.test_connection(db, proj.id)
        saved.append({
            "id": proj.id,
            "name": proj.name,
            "skipped": False,
            "connection_status": result["status"],
            "connection_error": result.get("detail"),
        })
    return {"imported": saved}
