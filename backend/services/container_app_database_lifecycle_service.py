"""Status and destructive lifecycle operations for managed app services."""
import secrets
from pathlib import Path

from fastapi import HTTPException

from models.container_app import ContainerApp
from models.container_app_database import ContainerAppDatabase
from services import container_app_database_service as databases
from services import container_app_service


def status(item: ContainerAppDatabase) -> dict[str, str]:
    if item.provider == "external":
        return {"status": "external", "detail": "The panel does not probe external databases."}
    if item.provider == "supabase":
        return {"status": item.status or "remote", "detail": "Remote Supabase database — no local container."}
    result = container_app_service._run(["docker", "inspect", "--format", "{{.State.Status}}", item.container_name or ""], timeout=15)
    return {"status": result.stdout.strip() if result.returncode == 0 else "missing", "detail": (result.stderr or "").strip()[-300:]}


def reconnect(item: ContainerAppDatabase) -> None:
    if item.provider != "docker":
        raise HTTPException(400, "Only Docker-managed databases can be reconnected here.")
    databases._require(container_app_service._run(["docker", "start", item.container_name or ""], timeout=45), "Could not start database.")
    item.status, item.last_error = "ready", None


def rotate_credentials(app: ContainerApp, item: ContainerAppDatabase) -> None:
    if item.provider == "external":
        raise HTTPException(400, "External database credentials are managed by their provider.")
    old, updated = databases._read_credentials(item), databases._read_credentials(item)
    updated["PASSWORD"] = secrets.token_urlsafe(30)
    if item.provider == "panel_postgres":
        from plugins.postgres_manager import queries as pg
        pg.change_password(item.username or "", updated["PASSWORD"])
    elif item.provider == "panel_mariadb":
        from plugins.mariadb_manager.service import mariadb_manager_service
        updated["PASSWORD"] = mariadb_manager_service.reset_password(item.username or "")
    elif item.kind == "redis":
        databases._require(container_app_service._run(["docker", "rm", "-f", item.container_name or ""], timeout=45), "Could not restart Redis.")
        databases._write_credentials(item, updated)
        databases._provision_docker(app, item)
        return
    else:
        command = _rotation_command(item, old, updated)
        databases._require(container_app_service._run(command, timeout=45), "Could not rotate database credentials.")
    databases._write_credentials(item, updated)


def delete_managed(item: ContainerAppDatabase, confirmation: str) -> None:
    expected = f"DELETE {item.kind.upper()} {item.id}"
    if confirmation != expected:
        raise HTTPException(400, f"Type {expected} to permanently delete this data.")
    if item.provider == "supabase":
        raise HTTPException(400, "Supabase databases are not deleted here — use the Supabase plugin or dashboard.")
    if item.provider != "docker":
        raise HTTPException(400, "Panel-hosted and external databases are not deleted here.")
    purge_managed(item)


def purge_managed(item: ContainerAppDatabase) -> None:
    if item.provider != "docker":
        raise HTTPException(400, "Only Docker-managed databases can be deleted here.")
    databases._require(container_app_service._run(["docker", "rm", "-f", item.container_name or ""], timeout=45), "Could not remove database container.")
    databases._require(container_app_service._run(["docker", "volume", "rm", item.volume_name or ""], timeout=45), "Could not remove database volume.")
    Path(item.credentials_path or "").unlink(missing_ok=True)


def _rotation_command(item, old, updated):
    if item.kind == "mariadb":
        return ["docker", "exec", item.container_name or "", "mariadb", "-uroot", f"-p{old['ROOT_PASSWORD']}", "-e", f"ALTER USER '{item.username}'@'%' IDENTIFIED BY '{updated['PASSWORD']}';"]
    if item.kind == "postgresql":
        return ["docker", "exec", item.container_name or "", "psql", "-U", item.username or "", "-d", item.database_name or "", "-c", f"ALTER USER {item.username} WITH PASSWORD '{updated['PASSWORD']}';"]
    return ["docker", "exec", item.container_name or "", "mongosh", "--authenticationDatabase", "admin", "-u", item.username or "", "-p", old["PASSWORD"], "--eval", f"db.updateUser('{item.username}', {{pwd: '{updated['PASSWORD']}'}})"]
