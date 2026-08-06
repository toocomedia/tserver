"""Immutable filesystem and systemd ownership for hosted Python apps."""
from __future__ import annotations

import socket
from pathlib import Path

from fastapi import HTTPException

import config
from models.hosted_app import HostedApp


def service_name(app_id: int) -> str:
    return f"srv-python-{app_id}"


def work_dir(app_id: int) -> Path:
    return Path(config.APP_HOSTING_ROOT) / str(app_id)


def env_path(app_id: int) -> Path:
    return Path(config.APP_HOSTING_ENV_ROOT) / f"{app_id}.env"


def unit_path(name: str) -> Path:
    return Path("/etc/systemd/system") / f"{name}.service"


def apply_identity(app: HostedApp) -> None:
    """Make the DB record point only at resources owned by this app id."""
    if app.id is None:
        raise HTTPException(500, "Python app identity has not been created.")
    app.service_name = service_name(app.id)
    app.work_dir = str(work_dir(app.id))
    app.env_path = str(env_path(app.id))


def require_environment(app: HostedApp) -> None:
    if not Path(app.env_path).is_file():
        detail = "Configuration file is missing. Redeploy or run the hosting repair command."
        if app.postgres_mode in {"external", "supabase"}:
            detail += " Re-save DATABASE_URL before repairing."
        raise HTTPException(409, detail)


def assert_unit_owner(app: HostedApp) -> None:
    unit = unit_path(app.service_name)
    if not unit.is_file():
        return
    try:
        content = unit.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(409, "Cannot verify the existing Python app service unit.") from exc
    expected = (f"Description=SRV Panel Python app {app.id}", f"EnvironmentFile={app.env_path}")
    if not all(value in content for value in expected):
        raise HTTPException(409, "Service ownership conflict. Run the hosting repair command before deploying.")


def require_port_free(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise HTTPException(409, f"Private port {port} is already used by another process.")
