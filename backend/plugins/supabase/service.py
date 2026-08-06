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


from urllib.parse import quote, unquote, urlsplit


def _project_ref(project: SupabaseProject) -> str:
    if project.project_ref:
        return project.project_ref
    parts = project.db_host.split(".")
    return parts[1] if len(parts) >= 2 and parts[0] == "db" else ""


def _pooler_host(region: str | None) -> str:
    """Build the shared Supavisor hostname from Management API metadata."""
    value = (region or "").strip().removesuffix(".")
    if not value:
        raise ValueError("Supabase project region is unavailable.")
    if value.endswith(".pooler.supabase.com"):
        return value
    if value.startswith("aws-"):
        return f"{value}.pooler.supabase.com"
    return f"aws-0-{value}.pooler.supabase.com"


def _dsn(project: SupabaseProject, db_name: str | None = None, use_pooler: bool = False) -> str:
    password = _decrypt_password(project)
    db = db_name or project.db_name
    pass_enc = quote(password or "", safe="")

    if use_pooler or "pooler.supabase.com" in project.db_host:
        ref = _project_ref(project)
        if "pooler.supabase.com" in project.db_host:
            host = project.db_host
            port = project.db_port
            user = project.db_user
        else:
            host = _pooler_host(project.region)
            # Session mode supports the persistent connections this manager uses.
            port = 5432
            user_base = project.db_user or "postgres"
            user = f"{user_base}.{ref}" if ref and not user_base.endswith(f".{ref}") else user_base

        user_enc = quote(user, safe="")
        return f"postgresql://{user_enc}:{pass_enc}@{host}:{port}/{db}"

    user_enc = quote(project.db_user or "postgres", safe="")
    return f"postgresql://{user_enc}:{pass_enc}@{project.db_host}:{project.db_port}/{db}"


def _should_use_pooler(project: SupabaseProject, exc: Exception) -> bool:
    if "pooler.supabase.com" in project.db_host:
        return False
    if isinstance(exc, asyncio.TimeoutError):
        return True
    error = str(exc).lower()
    markers = (
        "101", "unreachable", "timeout", "timed out", "cannot connect",
        "name or service not known", "temporary failure", "no address associated", "no route",
    )
    return isinstance(exc, (OSError, asyncpg.exceptions.CannotConnectNowError)) and any(
        marker in error for marker in markers
    )


async def _refresh_project_region(project: SupabaseProject) -> None:
    """Refresh region before using Supavisor so imported projects self-correct."""
    pat = _decrypt_pat(project)
    ref = _project_ref(project)
    if not pat or not ref:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{_MGMT_BASE}/projects/{ref}", headers=_mgmt_headers(pat)
            )
        if response.is_success and response.json().get("region"):
            project.region = response.json()["region"]
    except (httpx.HTTPError, ValueError):
        logger.info("Could not refresh Supabase region for project %s", ref)


def _pooler_settings(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = payload.get("data", [payload])
    else:
        return None
    settings = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        connection = entry.get("connectionString") or entry.get("connection_string")
        parsed = urlsplit(connection) if isinstance(connection, str) else None
        host = entry.get("db_host") or (parsed.hostname if parsed else None)
        port = entry.get("db_port") or (parsed.port if parsed else None)
        user = entry.get("db_user") or (unquote(parsed.username) if parsed and parsed.username else None)
        database = entry.get("db_name") or (parsed.path.lstrip("/") if parsed else None)
        if host and port and user and database:
            settings.append({"host": host, "port": int(port), "user": user, "database": database})
    return next((item for item in settings if item["port"] == 5432), settings[0] if settings else None)


async def _refresh_pooler_connection(project: SupabaseProject) -> str | None:
    """Read Supabase's assigned pooler hostname instead of guessing aws-0/1."""
    pat = _decrypt_pat(project)
    ref = _project_ref(project)
    if not pat or not ref:
        return "missing Personal Access Token or project reference"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{_MGMT_BASE}/projects/{ref}/config/database/pooler",
                headers=_mgmt_headers(pat),
            )
        if not response.is_success:
            return f"Supabase Pooler configuration returned HTTP {response.status_code}"
        settings = _pooler_settings(response.json())
        if not settings:
            return "Supabase returned no usable Session Pooler connection"
        project.db_host = settings["host"]
        project.db_port = settings["port"]
        project.db_user = settings["user"]
        project.db_name = settings["database"]
        return None
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("Could not refresh Supabase pooler connection for project %s: %s", ref, exc)
        return f"could not read Supabase Pooler configuration: {exc}"


async def _pg_connect(project: SupabaseProject, db_name: str | None = None):
    """Return a single asyncpg connection (caller must close). Auto-tries IPv4 Pooler if direct IPv6 is unreachable."""
    try:
        return await asyncio.wait_for(
            asyncpg.connect(_dsn(project, db_name), ssl="require"),
            timeout=_CONNECT_TIMEOUT,
        )
    except (OSError, asyncpg.exceptions.CannotConnectNowError, asyncio.TimeoutError) as exc:
        if not _should_use_pooler(project, exc):
            raise
        logger.info("Direct Supabase connection failed (%s), trying IPv4 Session Pooler...", exc)
        config_error = await _refresh_pooler_connection(project)
        if config_error:
            await _refresh_project_region(project)
        try:
            return await asyncio.wait_for(
                asyncpg.connect(_dsn(project, db_name, use_pooler=True), ssl="require"),
                timeout=_CONNECT_TIMEOUT,
            )
        except Exception as pooler_exc:
            endpoint = f"{project.db_host}:{project.db_port} as {project.db_user}"
            detail = f"Pooler endpoint {endpoint}."
            if config_error:
                detail += f" Auto-discovery: {config_error}."
            raise ConnectionError(f"{pooler_exc} ({detail})") from pooler_exc


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
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    pat: str | None = None,
    region: str | None = None,
) -> SupabaseProject:
    proj = await get_project(db, project_id)
    secret = _secret()
    if name is not None:
        proj.name = name
    if db_host is not None:
        proj.db_host = db_host
    if db_port is not None:
        proj.db_port = db_port
    if db_user is not None:
        proj.db_user = db_user
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
        logger.exception("Supabase connection test failed for project %s (%s)", project_id, proj.db_host)
        return {"status": "error", "detail": str(exc)}


async def _safe_pg_connect(project: SupabaseProject, db_name: str | None = None):
    """Return a single asyncpg connection or raise clean HTTPException(400) on error."""
    try:
        return await _pg_connect(project, db_name)
    except Exception as exc:
        from fastapi import HTTPException
        logger.warning("Database connection failed for project %s (%s): %s", project.id, project.db_host, exc)
        raise HTTPException(400, f"Database connection error: {exc}") from exc


# ──────────────────────────────────────────────
# Database & table browser
# ──────────────────────────────────────────────

async def list_databases(project_id: int, db: AsyncSession) -> list[dict[str, Any]]:
    proj = await get_project(db, project_id)
    conn = await _safe_pg_connect(proj)
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
    conn = await _safe_pg_connect(proj, db_name=database)
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
    conn = await _safe_pg_connect(proj)
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
    conn = await _safe_pg_connect(proj, db_name=database)
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
        if r.status_code == 400 and "already" in r.text.lower():
            return {"status": "paused", "detail": "Project is already paused."}
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
        if r.status_code == 400 and ("active" in r.text.lower() or "paused" in r.text.lower()):
            return {"status": "active", "detail": "Project is already active."}
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
