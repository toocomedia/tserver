"""Database-provider setup for container apps; managed data survives app deletion."""
from __future__ import annotations

import secrets
from pathlib import Path
import subprocess
from urllib.parse import quote

from fastapi import HTTPException

from dependencies import dependency_manager
from models.container_app import ContainerApp
from plugins.postgres_manager import queries as pg
from services import container_app_service


def panel_postgres_available() -> bool:
    return dependency_manager.is_healthy("postgresql")


def provision_panel_postgres(app: ContainerApp, values: dict[str, str]) -> dict[str, str]:
    if not panel_postgres_available():
        raise HTTPException(409, "Start PostgreSQL from Dependencies before using panel PostgreSQL.")
    database, username = f"app_{app.id}", f"app_{app.id}"
    password = secrets.token_urlsafe(32)
    try:
        _allow_container_networks()
        pg.create_user(username, password)
        pg.create_database(database, username)
    except Exception as exc:
        try:
            pg.drop_app_database_and_user(database, username)
        except Exception:
            pass
        raise HTTPException(502, "Could not create the panel PostgreSQL database.") from exc
    app.database_provider, app.database_name, app.database_user = "postgres_manager", database, username
    return {
        **values,
        "DATABASE_URL": f"postgresql://{quote(username)}:{quote(password)}@host.docker.internal:5432/{database}",
    }


def _allow_container_networks() -> None:
    script = Path(__file__).resolve().parents[1] / "plugins" / "postgres_manager" / "scripts" / "allow-container-apps"
    try:
        result = container_app_service._run(["bash", str(script)], timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(502, "Could not configure the PostgreSQL container bridge.") from exc
    if result.returncode:
        raise HTTPException(502, "Could not configure the PostgreSQL container bridge.")


def write_app_environment(app: ContainerApp, values: dict[str, str]) -> None:
    container_app_service.write_env(
        Path(app.env_path), container_app_service.environment_for_port(values, app.internal_port),
    )
