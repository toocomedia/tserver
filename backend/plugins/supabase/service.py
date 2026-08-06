"""Supabase plugin business logic.

Two clients per project:
  1. asyncpg direct connection → db.<ref>.supabase.co:5432
  2. httpx async → https://api.supabase.com/v1  (Management API, PAT auth)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from models.supabase_project import SupabaseProject
from plugins.supabase.crypto import decrypt, encrypt

logger = logging.getLogger(__name__)

_MGMT_BASE = "https://api.supabase.com/v1"
_CONNECT_TIMEOUT = 10  # seconds


# ──────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────

def _secret() -> str:
    return config.SECRET_KEY


def _decrypt_password(project: SupabaseProject) -> str:
    return decrypt(project.db_password_enc, _secret())


def _decrypt_pat(project: SupabaseProject) -> str | None:
    if not project.pat_enc:
        return None
    return decrypt(project.pat_enc, _secret())


def _dsn(project: SupabaseProject, db_name: str | None = None) -> str:
    password = _decrypt_password(project)
    db = db_name or project.db_name
    return (
        f"postgresql://{project.db_user}:{password}"
        f"@{project.db_host}:{project.db_port}/{db}"
    )


async def _pg_connect(project: SupabaseProject, db_name: str | None = None):
    """Return a single asyncpg connection (caller must close)."""
    return await asyncio.wait_for(
        asyncpg.connect(_dsn(project, db_name)),
        timeout=_CONNECT_TIMEOUT,
    )


def _mgmt_headers(pat: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {pat}", "Content-Type": "application/json"}


# ──────────────────────────────────────────────
# PAT: fetch all org projects from Supabase API
# ──────────────────────────────────────────────

async def fetch_account_projects(pat: str) -> list[dict[str, Any]]:
    """Call Supabase Management API with a PAT and return all projects."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{_MGMT_BASE}/projects", headers=_mgmt_headers(pat))
        if r.status_code == 401:
            from fastapi import HTTPException
            raise HTTPException(401, "Invalid Personal Access Token.")
        if not r.is_success:
            from fastapi import HTTPException
            raise HTTPException(502, f"Supabase API error {r.status_code}: {r.text[:200]}")
        raw = r.json()
        return [
            {
                "ref": p.get("id", ""),
                "name": p.get("name", ""),
                "region": p.get("region", ""),
                "status": p.get("status", ""),
                "db_host": f"db.{p.get('id', '')}.supabase.co",
                "db_port": 5432,
                "db_name": "postgres",
                "db_user": "postgres",
            }
            for p in raw
        ]


# ──────────────────────────────────────────────
# Project CRUD (panel DB)
# ──────────────────────────────────────────────

async def list_projects(db: AsyncSession) -> list[SupabaseProject]:
    res = await db.scalars(
        select(SupabaseProject).order_by(SupabaseProject.created_at.desc())
    )
    return list(res.all())


async def get_project(db: AsyncSession, project_id: int) -> SupabaseProject:
    proj = await db.get(SupabaseProject, project_id)
    if not proj:
        from fastapi import HTTPException
        raise HTTPException(404, "Supabase project not found.")
    return proj


async def create_project(
    db: AsyncSession,
    name: str,
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_password: str,
    pat: str | None,
    region: str | None,
) -> SupabaseProject:
    secret = _secret()
    proj = SupabaseProject(
        name=name,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password_enc=encrypt(db_password, secret),
        pat_enc=encrypt(pat, secret) if pat else None,
        region=region,
        connection_status="unknown",
    )
    # Extract project_ref from host (db.<ref>.supabase.co)
    parts = db_host.split(".")
    if len(parts) >= 2 and parts[0] == "db":
        proj.project_ref = parts[1]
    db.add(proj)
    await db.flush()
    return proj


async def delete_project(db: AsyncSession, project_id: int) -> None:
    proj = await get_project(db, project_id)
    await db.delete(proj)


async def update_project(
    db: AsyncSession,
    project_id: int,
    name: str | None = None,
    db_password: str | None = None,
    pat: str | None = None,
    region: str | None = None,
) -> SupabaseProject:
    proj = await get_project(db, project_id)
    secret = _secret()
    if name is not None:
        proj.name = name
    if db_password is not None:
        proj.db_password_enc = encrypt(db_password, secret)
    if pat is not None:
        proj.pat_enc = encrypt(pat, secret)
    if region is not None:
        proj.region = region
    return proj


# ──────────────────────────────────────────────
# Connection test
# ──────────────────────────────────────────────

async def test_connection(db: AsyncSession, project_id: int) -> dict[str, Any]:
    proj = await get_project(db, project_id)
    try:
        conn = await _pg_connect(proj)
        row = await conn.fetchrow("SELECT version()")
        await conn.close()
        version = row["version"] if row else "unknown"
        proj.connection_status = "ok"
        proj.last_connected_at = datetime.now(timezone.utc)
        return {"status": "ok", "version": version}
    except Exception as exc:
        proj.connection_status = "error"
        logger.warning("Supabase connection test failed for project %s: %s", project_id, exc)
        return {"status": "error", "detail": str(exc)}


# ──────────────────────────────────────────────
# Database & table browser
# ──────────────────────────────────────────────

async def list_databases(project_id: int, db: AsyncSession) -> list[dict[str, Any]]:
    proj = await get_project(db, project_id)
    conn = await _pg_connect(proj)
    try:
        rows = await conn.fetch(
            """
            SELECT datname AS name,
                   pg_catalog.pg_encoding_to_char(encoding) AS encoding,
                   pg_size_pretty(pg_database_size(datname)) AS size
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY datname
            """
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def list_tables(
    project_id: int, database: str, db: AsyncSession
) -> list[dict[str, Any]]:
    proj = await get_project(db, project_id)
    conn = await _pg_connect(proj, db_name=database)
    try:
        rows = await conn.fetch(
            """
            SELECT schemaname AS schema,
                   tablename  AS name,
                   pg_size_pretty(
                       pg_total_relation_size(schemaname || '.' || tablename)
                   ) AS size
            FROM pg_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY schemaname, tablename
            """
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def list_roles(project_id: int, db: AsyncSession) -> list[dict[str, Any]]:
    proj = await get_project(db, project_id)
    conn = await _pg_connect(proj)
    try:
        rows = await conn.fetch(
            """
            SELECT rolname AS name,
                   rolsuper AS superuser,
                   rolcanlogin AS can_login,
                   rolcreatedb AS create_db
            FROM pg_roles
            ORDER BY rolname
            """
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


# ──────────────────────────────────────────────
# Query runner (SELECT only — enforced in schemas)
# ──────────────────────────────────────────────

async def run_query(
    project_id: int, database: str, sql: str, db: AsyncSession
) -> dict[str, Any]:
    proj = await get_project(db, project_id)
    conn = await _pg_connect(proj, db_name=database)
    try:
        rows = await conn.fetch(sql)
        data = [dict(r) for r in rows]
        return {"rows": data, "count": len(data)}
    finally:
        await conn.close()


# ──────────────────────────────────────────────
# Management API
# ──────────────────────────────────────────────

async def get_project_stats(project_id: int, db: AsyncSession) -> dict[str, Any]:
    proj = await get_project(db, project_id)
    pat = _decrypt_pat(proj)
    if not pat:
        return {"error": "No PAT configured for this project."}
    if not proj.project_ref:
        return {"error": "No project_ref detected — set db_host to db.<ref>.supabase.co."}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{_MGMT_BASE}/projects/{proj.project_ref}",
            headers=_mgmt_headers(pat),
        )
        if r.status_code != 200:
            return {"error": f"Management API {r.status_code}: {r.text[:200]}"}
        data = r.json()
        return {
            "name": data.get("name"),
            "status": data.get("status"),
            "region": data.get("region"),
            "created_at": data.get("created_at"),
            "plan": data.get("subscription_id"),
        }


async def pause_project(project_id: int, db: AsyncSession) -> dict[str, Any]:
    proj = await get_project(db, project_id)
    pat = _decrypt_pat(proj)
    if not pat or not proj.project_ref:
        from fastapi import HTTPException
        raise HTTPException(400, "PAT and project_ref required for pause/restore.")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{_MGMT_BASE}/projects/{proj.project_ref}/pause",
            headers=_mgmt_headers(pat),
        )
        if r.status_code not in (200, 201, 204):
            from fastapi import HTTPException
            raise HTTPException(502, f"Supabase API error {r.status_code}: {r.text[:200]}")
        return {"status": "paused"}


async def restore_project(project_id: int, db: AsyncSession) -> dict[str, Any]:
    proj = await get_project(db, project_id)
    pat = _decrypt_pat(proj)
    if not pat or not proj.project_ref:
        from fastapi import HTTPException
        raise HTTPException(400, "PAT and project_ref required for pause/restore.")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{_MGMT_BASE}/projects/{proj.project_ref}/restore",
            headers=_mgmt_headers(pat),
        )
        if r.status_code not in (200, 201, 204):
            from fastapi import HTTPException
            raise HTTPException(502, f"Supabase API error {r.status_code}: {r.text[:200]}")
        return {"status": "restoring"}


# ──────────────────────────────────────────────
# App provisioning (called by container_app_database_service)
# ──────────────────────────────────────────────

async def provision_app_database(
    project_id: int,
    database_name: str,
    username: str,
    password: str,
    db: AsyncSession,
) -> str:
    """Create a PG user + database on Supabase for a hosted app.
    Returns the DATABASE_URL to inject into the app .env.
    """
    proj = await get_project(db, project_id)
    conn = await _pg_connect(proj)
    try:
        # Create user
        await conn.execute(
            f"CREATE USER {username} WITH PASSWORD $1 LOGIN",
            password,
        )
        # Create database owned by that user
        await conn.execute(f"CREATE DATABASE {database_name} OWNER {username}")
    finally:
        await conn.close()

    # Build and return DATABASE_URL
    from urllib.parse import quote
    pwd_enc = quote(password, safe="")
    usr_enc = quote(username, safe="")
    return (
        f"postgresql://{usr_enc}:{pwd_enc}"
        f"@{proj.db_host}:{proj.db_port}/{database_name}"
    )


async def deprovision_app_database(
    project_id: int,
    database_name: str,
    username: str,
    db: AsyncSession,
) -> None:
    """Drop database + user created by provision_app_database."""
    proj = await get_project(db, project_id)
    conn = await _pg_connect(proj)
    try:
        await conn.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
            database_name,
        )
        await conn.execute(f"DROP DATABASE IF EXISTS {database_name}")
        await conn.execute(f"DROP USER IF EXISTS {username}")
    finally:
        await conn.close()
