"""Private managed database lifecycle for Railpack applications."""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from dependencies import dependency_manager
from models.container_app import ContainerApp
from models.container_app_database import ContainerAppDatabase
from services import container_app_service

KINDS = {"mariadb", "postgresql", "redis", "mongodb"}
PROVIDERS = {"docker", "panel_postgres", "external"}
DEFAULT_KEYS = {"mariadb": "MYSQL_URL", "postgresql": "DATABASE_URL", "redis": "REDIS_URL", "mongodb": "MONGODB_URI"}
IMAGES = {"mariadb": "mariadb:11", "postgresql": "postgres:16-alpine", "redis": "redis:7-alpine", "mongodb": "mongo:7"}


def credentials_path(database_id: int) -> Path:
    return Path(config.CONTAINER_APP_ENV_ROOT) / "databases" / f"{database_id}.env"


def parse_specs(raw: object) -> list[dict[str, str]]:
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list) or len(raw) > len(KINDS):
        raise HTTPException(400, "Database attachments are invalid.")
    result, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise HTTPException(400, "Database attachments are invalid.")
        kind, provider = str(item.get("kind", "")), str(item.get("provider", ""))
        key = str(item.get("environment_key") or DEFAULT_KEYS.get(kind, ""))
        if kind not in KINDS or provider not in PROVIDERS or kind in seen or not key.isidentifier() or not key.isupper():
            raise HTTPException(400, "Database attachment values are invalid.")
        if provider == "panel_postgres" and kind != "postgresql":
            raise HTTPException(400, "Panel PostgreSQL is only available for PostgreSQL attachments.")
        external_url = str(item.get("external_url", ""))
        if provider == "external" and (not external_url or not urlsplit(external_url).scheme):
            raise HTTPException(400, "Each external database needs a connection URL.")
        result.append({"kind": kind, "provider": provider, "environment_key": key, "external_url": external_url})
        seen.add(kind)
    return result


async def create_attachments(db: AsyncSession, app: ContainerApp, specs: list[dict[str, str]]) -> list[ContainerAppDatabase]:
    attachments = []
    for spec in specs:
        item = ContainerAppDatabase(app_id=app.id, kind=spec["kind"], provider=spec["provider"], environment_key=spec["environment_key"])
        db.add(item)
        await db.flush()
        if item.provider == "external":
            attachments.append(item)
            continue
        item.credentials_path = str(credentials_path(item.id))
        _write_credentials(item, _new_credentials(item))
        if item.provider == "panel_postgres":
            _provision_panel_postgres(app, item)
        else:
            _provision_docker(app, item)
        item.status, item.last_error = "ready", None
        attachments.append(item)
    return attachments


async def attachments_for(db: AsyncSession, app_id: int) -> list[ContainerAppDatabase]:
    result = await db.scalars(select(ContainerAppDatabase).where(
        ContainerAppDatabase.app_id == app_id).order_by(ContainerAppDatabase.id))
    return list(result.all())


def rebuild_environment(app: ContainerApp, attachments: list[ContainerAppDatabase], values: dict[str, str]) -> None:
    for item in attachments:
        if item.provider == "external":
            continue
        values.pop(item.environment_key, None)
        values[item.environment_key] = connection_url(item)
        if app.preset == "wordpress" and item.kind == "mariadb":
            creds = _read_credentials(item)
            values.update({"WORDPRESS_DB_HOST": f"{item.network_alias}:3306", "WORDPRESS_DB_NAME": item.database_name or "", "WORDPRESS_DB_USER": item.username or "", "WORDPRESS_DB_PASSWORD": creds["PASSWORD"]})
    container_app_service.write_env(Path(app.env_path), container_app_service.environment_for_port(values, app.internal_port))


def read_app_environment(app: ContainerApp) -> dict[str, str]:
    path = Path(app.env_path)
    if not path.is_file():
        return {}
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def connection_url(item: ContainerAppDatabase) -> str:
    creds = _read_credentials(item)
    password = quote(creds["PASSWORD"], safe="")
    if item.kind == "mariadb":
        return f"mysql://{quote(item.username or '', safe='')}:{password}@{item.network_alias}:3306/{item.database_name}"
    if item.kind == "postgresql":
        return f"postgresql://{quote(item.username or '', safe='')}:{password}@{item.network_alias or 'host.docker.internal'}:5432/{item.database_name}"
    if item.kind == "redis":
        return f"redis://:{password}@{item.network_alias}:6379/0"
    return f"mongodb://{quote(item.username or '', safe='')}:{password}@{item.network_alias}:27017/{item.database_name}?authSource=admin"


def _new_credentials(item: ContainerAppDatabase) -> dict[str, str]:
    token = secrets.token_urlsafe(30)
    name = f"app_{item.app_id}_{item.kind}"[:63]
    return {"PASSWORD": token, "ROOT_PASSWORD": secrets.token_urlsafe(30), "USERNAME": name, "DATABASE": name}


def _build_docker_env_file(item: ContainerAppDatabase) -> None:
    """Write a second env file with DB-engine-specific variable names.

    Credentials are never passed as -e CLI arguments to avoid leaking them
    in 'docker inspect', process listings, or audit/sudo logs.
    The credentials_path file already stores PASSWORD / ROOT_PASSWORD /
    USERNAME / DATABASE.  This function writes engine-native names into the
    same file so the container picks them up automatically via --env-file.
    """
    creds = _read_credentials(item)
    extra: dict[str, str] = {}
    if item.kind == "mariadb":
        extra = {
            "MYSQL_DATABASE": creds["DATABASE"],
            "MYSQL_USER": creds["USERNAME"],
            "MYSQL_PASSWORD": creds["PASSWORD"],
            "MYSQL_ROOT_PASSWORD": creds["ROOT_PASSWORD"],
        }
    elif item.kind == "postgresql":
        extra = {
            "POSTGRES_DB": creds["DATABASE"],
            "POSTGRES_USER": creds["USERNAME"],
            "POSTGRES_PASSWORD": creds["PASSWORD"],
        }
    elif item.kind == "mongodb":
        extra = {
            "MONGO_INITDB_ROOT_USERNAME": creds["USERNAME"],
            "MONGO_INITDB_ROOT_PASSWORD": creds["PASSWORD"],
        }
    # redis uses PASSWORD directly from the base credentials file
    if extra:
        merged = {**creds, **extra}
        _write_credentials(item, merged)


def _provision_docker(app: ContainerApp, item: ContainerAppDatabase) -> None:
    if not dependency_manager.is_healthy("docker"):
        raise HTTPException(409, "Docker daemon is not available.")
    item.container_name = f"srv-container-db-{app.id}-{item.kind}"
    item.volume_name, item.network_alias = f"srv-container-db-data-{item.id}", f"db-{item.kind}"
    creds = _read_credentials(item)
    item.database_name, item.username = creds["DATABASE"], creds["USERNAME"]
    _build_docker_env_file(item)  # merge engine-native names into the env file
    _network(app)
    _require(
        container_app_service._run(
            ["docker", "volume", "create",
             "--label", "srv-panel.plugin=railpack_apps",
             "--label", f"srv-panel.app-id={app.id}",
             item.volume_name],
            timeout=30,
        ),
        "Could not create database volume.",
    )
    # Base command — credentials only via --env-file, never -e
    command = [
        "docker", "run", "-d",
        "--name", item.container_name,
        "--restart", "unless-stopped",
        "--label", "srv-panel.plugin=railpack_apps",
        "--label", f"srv-panel.app-id={app.id}",
        "--network", container_app_service.network_name(app.id),
        "--network-alias", item.network_alias,
        "--env-file", item.credentials_path or "",
    ]
    if item.kind == "mariadb":
        command += ["-v", f"{item.volume_name}:/var/lib/mysql"]
    elif item.kind == "postgresql":
        command += ["-v", f"{item.volume_name}:/var/lib/postgresql/data"]
    elif item.kind == "redis":
        command += [
            "-v", f"{item.volume_name}:/data",
            IMAGES[item.kind], "sh", "-c",
            'exec redis-server --appendonly yes --requirepass "$PASSWORD"',
        ]
        _require(container_app_service._run(command, timeout=60), "Could not start database container.")
        return
    else:  # mongodb
        command += ["-v", f"{item.volume_name}:/data/db"]
    _require(
        container_app_service._run([*command, IMAGES[item.kind]], timeout=60),
        "Could not start database container.",
    )


def _provision_panel_postgres(app: ContainerApp, item: ContainerAppDatabase) -> None:
    if not dependency_manager.is_healthy("postgresql"):
        raise HTTPException(409, "Start PostgreSQL from Dependencies before using panel PostgreSQL.")
    from plugins.postgres_manager import queries as pg
    _allow_panel_postgres_network()
    creds = _read_credentials(item)
    item.database_name, item.username, item.network_alias = creds["DATABASE"], creds["USERNAME"], "host.docker.internal"
    pg.create_user(item.username, creds["PASSWORD"])
    try:
        pg.create_database(item.database_name, item.username)
    except Exception:
        pg.drop_app_database_and_user(item.database_name, item.username)
        raise


def _allow_panel_postgres_network() -> None:
    script = Path(__file__).resolve().parents[1] / "plugins" / "postgres_manager" / "scripts" / "allow-container-apps"
    result = container_app_service._run(["bash", str(script)], timeout=45)
    _require(result, "Could not configure the PostgreSQL container bridge.")


def _network(app: ContainerApp) -> None:
    result = container_app_service._run(["docker", "network", "inspect", container_app_service.network_name(app.id)], timeout=15)
    if result.returncode:
        _require(container_app_service._run(["docker", "network", "create", "--driver", "bridge", "--label", "srv-panel.plugin=railpack_apps", "--label", f"srv-panel.app-id={app.id}", container_app_service.network_name(app.id)], timeout=30), "Could not create private app network.")


def _write_credentials(item: ContainerAppDatabase, values: dict[str, str]) -> None:
    path = credentials_path(item.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    container_app_service.write_env(path, values)


def _read_credentials(item: ContainerAppDatabase) -> dict[str, str]:
    path = Path(item.credentials_path or "")
    if not path.is_file():
        raise HTTPException(409, "Database credentials are unavailable. Reconnect or recreate this attachment.")
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def _require(result, message: str) -> None:
    if result.returncode:
        raise HTTPException(502, (result.stderr or result.stdout or message)[-1000:])
