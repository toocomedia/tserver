"""FastAPI router for the Supabase plugin.

UI  routes: GET  /plugins/supabase/
API routes:     /plugins/supabase/api/*
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
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
    try:
        databases = await svc.list_databases(project_id, db)
    except Exception:
        pass
    return templates.TemplateResponse(
        "supabase/project_detail.html",
        {"request": request, "project": project, "databases": databases},
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
