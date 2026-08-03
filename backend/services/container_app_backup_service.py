"""Manual local backup and restore operations for managed container data."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import config
from models.container_app import ContainerApp
from models.container_app_backup import ContainerAppBackup
from models.container_app_database import ContainerAppDatabase
from services import container_app_database_service as databases
from services import container_app_service


async def create_database_backup(db: AsyncSession, app: ContainerApp, item: ContainerAppDatabase) -> ContainerAppBackup:
    if item.provider != "docker":
        raise HTTPException(400, "Only Docker-managed databases have local backups here.")
    backup = ContainerAppBackup(app_id=app.id, database_id=item.id, kind=item.kind, path="pending", status="running")
    db.add(backup)
    await db.flush()
    path = _path(app.id, backup.id, item.kind)
    backup.path = str(path)
    try:
        await asyncio.to_thread(_dump, item, path)
        backup.status = "complete"
    except Exception as exc:
        backup.status = "failed"
        raise HTTPException(502, f"Backup failed: {exc}") from exc
    return backup


async def restore_database_backup(db: AsyncSession, app: ContainerApp, item: ContainerAppDatabase, backup: ContainerAppBackup, confirmation: str) -> None:
    if backup.app_id != app.id or backup.database_id != item.id or backup.status != "complete":
        raise HTTPException(404, "Backup not found.")
    if confirmation != f"RESTORE {backup.id}":
        raise HTTPException(400, f"Type RESTORE {backup.id} to overwrite this database.")
    await create_database_backup(db, app, item)
    try:
        await asyncio.to_thread(_restore, item, Path(backup.path))
    except Exception as exc:
        raise HTTPException(502, f"Restore failed: {exc}") from exc


async def create_wordpress_backup(db: AsyncSession, app: ContainerApp, item: ContainerAppDatabase) -> ContainerAppBackup:
    database_backup = await create_database_backup(db, app, item)
    backup = ContainerAppBackup(app_id=app.id, database_backup_id=database_backup.id, kind="wordpress", path="pending", status="running")
    db.add(backup)
    await db.flush()
    path = _path(app.id, backup.id, "wordpress-content")
    backup.path = str(path)
    try:
        await asyncio.to_thread(_archive_volume, app.wordpress_content_volume or "", path)
        backup.status = "complete"
    except Exception as exc:
        backup.status = "failed"
        raise HTTPException(502, f"WordPress content backup failed: {exc}") from exc
    return backup


async def restore_wordpress_backup(db: AsyncSession, app: ContainerApp, item: ContainerAppDatabase, backup: ContainerAppBackup, confirmation: str) -> None:
    if backup.app_id != app.id or backup.kind != "wordpress" or backup.status != "complete":
        raise HTTPException(404, "WordPress backup not found.")
    if confirmation != f"RESTORE {backup.id}":
        raise HTTPException(400, f"Type RESTORE {backup.id} to overwrite WordPress data.")
    database_backup = await db.get(ContainerAppBackup, backup.database_backup_id)
    if database_backup is None:
        raise HTTPException(409, "The linked WordPress database backup is missing.")
    await create_wordpress_backup(db, app, item)
    await restore_database_backup(db, app, item, database_backup, f"RESTORE {database_backup.id}")
    await asyncio.to_thread(_restore_volume, app.wordpress_content_volume or "", Path(backup.path))


def _path(app_id: int, backup_id: int, kind: str) -> Path:
    root = Path(config.CONTAINER_APP_BACKUP_ROOT) / str(app_id)
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    return root / f"{backup_id}-{kind}.dump"


def _dump(item: ContainerAppDatabase, path: Path) -> None:
    creds = databases._read_credentials(item)
    commands = {
        "mariadb": ["docker", "exec", item.container_name or "", "mariadb-dump", "-uroot", f"-p{creds['ROOT_PASSWORD']}", "--all-databases"],
        "postgresql": ["docker", "exec", "-e", f"PGPASSWORD={creds['PASSWORD']}", item.container_name or "", "pg_dump", "-U", item.username or "", item.database_name or ""],
        "mongodb": ["docker", "exec", item.container_name or "", "mongodump", "--archive", "--authenticationDatabase", "admin", "-u", item.username or "", "-p", creds["PASSWORD"]],
    }
    if item.kind == "redis":
        result = container_app_service._run(["docker", "exec", item.container_name or "", "redis-cli", "-a", creds["PASSWORD"], "SAVE"], timeout=45)
        if result.returncode:
            raise RuntimeError(result.stderr or "Redis save failed.")
        result = container_app_service._run(["docker", "cp", f"{item.container_name}:/data/dump.rdb", str(path)], timeout=45)
        if result.returncode:
            raise RuntimeError(result.stderr or "Redis copy failed.")
        return
    result = container_app_service._run(commands[item.kind], timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr or "Database dump failed.")
    path.write_text(result.stdout, encoding="utf-8")
    path.chmod(0o600)


def _restore(item: ContainerAppDatabase, path: Path) -> None:
    if not path.is_file():
        raise RuntimeError("Backup file is missing.")
    if item.kind == "redis":
        _require(container_app_service._run(["docker", "cp", str(path), f"{item.container_name}:/data/dump.rdb"], timeout=45))
        _require(container_app_service._run(["docker", "restart", item.container_name or ""], timeout=60))
        return
    creds = databases._read_credentials(item)
    commands = {
        "mariadb": ["docker", "exec", "-i", item.container_name or "", "mariadb", "-uroot", f"-p{creds['ROOT_PASSWORD']}"],
        "postgresql": ["docker", "exec", "-i", "-e", f"PGPASSWORD={creds['PASSWORD']}", item.container_name or "", "psql", "-U", item.username or "", "-d", item.database_name or ""],
        "mongodb": ["docker", "exec", "-i", item.container_name or "", "mongorestore", "--drop", "--archive", "--authenticationDatabase", "admin", "-u", item.username or "", "-p", creds["PASSWORD"]],
    }
    prefix = ["sudo", "-n"] if hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO else []
    with path.open("rb") as source:
        result = subprocess.run([*prefix, *commands[item.kind]], stdin=source, capture_output=True, timeout=300, check=False, shell=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).decode(errors="replace")[-1000:])


def _require(result) -> None:
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Docker command failed.")[-1000:])


def _archive_volume(volume: str, path: Path) -> None:
    if not volume:
        raise RuntimeError("WordPress content volume is missing.")
    _binary_to_file(["docker", "run", "--rm", "-v", f"{volume}:/source:ro", "alpine:3.20", "tar", "-C", "/source", "-czf", "-", "."], path)


def _restore_volume(volume: str, path: Path) -> None:
    if not volume or not path.is_file():
        raise RuntimeError("WordPress content backup is unavailable.")
    _binary_from_file(["docker", "run", "--rm", "-i", "-v", f"{volume}:/target", "alpine:3.20", "sh", "-c", "rm -rf /target/* /target/.[!.]* /target/..?*; tar -C /target -xzf -"], path)


def _binary_to_file(command: list[str], path: Path) -> None:
    prefix = ["sudo", "-n"] if hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO else []
    with path.open("wb") as target:
        result = subprocess.run([*prefix, *command], stdout=target, stderr=subprocess.PIPE, timeout=300, check=False, shell=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-1000:])
    path.chmod(0o600)


def _binary_from_file(command: list[str], path: Path) -> None:
    prefix = ["sudo", "-n"] if hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO else []
    with path.open("rb") as source:
        result = subprocess.run([*prefix, *command], stdin=source, stderr=subprocess.PIPE, timeout=300, check=False, shell=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-1000:])
