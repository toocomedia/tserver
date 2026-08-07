"""Build and systemd runtime operations for one immutable app release."""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import secrets

from fastapi import HTTPException

import config
from models.hosted_app import HostedApp
from services import app_project_detector
from services import app_ownership_service
from utils import shell


async def build_release(app: HostedApp, release: Path, reporter=None) -> None:
    source, venv = release / "source", release / ".venv"
    await _progress(reporter, "venv", "Creating an isolated release environment.")
    result = await shell.run_unprivileged(
        ["python3", "-m", "venv", str(venv)], timeout=90
    )
    if not result.success:
        raise HTTPException(500, _tail(result.stderr, "Virtualenv creation failed."))
    await _progress(reporter, "dependencies", "Installing release dependencies.")
    command = f"cd {source} && PATH={venv}/bin:$PATH {app.build_command}"
    result = await shell.run_unprivileged(["bash", "-lc", command], timeout=600)
    if not result.success:
        raise HTTPException(400, _tail(result.stderr or result.stdout, "Build failed."))


async def prepare_environment(app: HostedApp, source: Path) -> None:
    env_path = Path(app.env_path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if app.postgres_mode == "create":
        existing = _managed_database(app, source, existing)
    elif app.postgres_mode == "supabase":
        existing = _replace_database_url_scheme(existing, source)
    env_path.write_text(_with_runtime_settings(app, existing), encoding="utf-8")
    os.chmod(env_path, 0o600)


async def install_unit(app: HostedApp, _release: Path) -> None:
    app_ownership_service.assert_unit_owner(app)
    current = Path(app.work_dir) / "current"
    source, venv = current / "source", current / ".venv"
    start = f"{venv}/bin/{app.start_command}".replace("'", "'\"'\"'")
    unit = (
        "[Unit]\n"
        f"Description=SRV Panel Python app {app.id}\n"
        "After=network.target\n\n"
        "[Service]\nType=simple\n"
        f"User={config.APP_HOSTING_USER}\nGroup={config.APP_HOSTING_USER}\n"
        f"WorkingDirectory={source}\nEnvironmentFile={app.env_path}\n"
        f"Environment=HOME={_service_home()}\n"
        f"ExecStart=/bin/bash -lc '{start}'\n"
        "Restart=on-failure\n\n[Install]\nWantedBy=multi-user.target\n"
    )
    await shell.write_file(service_unit(app), unit)
    await systemctl("daemon-reload")


def snapshot_environment(app: HostedApp) -> bytes | None:
    path = Path(app.env_path)
    return path.read_bytes() if path.is_file() else None


def restore_environment(
    app: HostedApp, snapshot: bytes | None, source: Path | None = None
) -> None:
    path = Path(app.env_path)
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    existing = snapshot.decode("utf-8")
    if app.postgres_mode == "supabase" and source is not None:
        existing = _replace_database_url_scheme(existing, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _with_runtime_settings(app, existing), encoding="utf-8"
    )
    os.chmod(path, 0o600)


async def start(app: HostedApp) -> None:
    app_ownership_service.require_environment(app)
    app_ownership_service.require_port_free(app.port)
    await systemctl("reset-failed", app.service_name, allow_missing=True)
    await systemctl("enable", "--now", app.service_name)


async def stop(app: HostedApp, *, allow_missing: bool = True) -> bool:
    return await systemctl("stop", app.service_name, allow_missing=allow_missing)


async def systemctl(*args: str, allow_missing: bool = False) -> bool:
    result = await shell.run(["systemctl", *args], timeout=30)
    message = result.stderr or result.stdout
    missing = any(
        text in message.lower()
        for text in ("not loaded", "not found", "does not exist")
    )
    if allow_missing and missing:
        return False
    if not result.success:
        raise HTTPException(500, message or "System service action failed.")
    return True


def service_unit(app: HostedApp) -> Path:
    return Path("/etc/systemd/system") / f"{app.service_name}.service"


def _service_home() -> str:
    try:
        return pwd.getpwnam(config.APP_HOSTING_USER).pw_dir
    except KeyError:
        return "/"


def validate_commands(build_command: str, start_command: str) -> None:
    for command in (build_command, start_command):
        if not command.strip() or len(command) > 1000:
            raise HTTPException(400, "Build and start commands are required and limited to 1000 characters.")
        if any(char in command for char in ("\n", "\r", "\x00")):
            raise HTTPException(400, "Commands must be a single line.")


def _managed_database(app: HostedApp, source: Path, existing: str) -> str:
    from plugins.postgres_manager import queries as pg
    scheme = _database_url_scheme(source)
    if not app.database_name:
        app.database_name, app.database_user = f"app{app.id}", f"app{app.id}"
        password = secrets.token_urlsafe(24)
        pg.create_user(app.database_user, password)
        pg.create_database(app.database_name, app.database_user)
        return existing + _database_url(scheme, app, password)
    if "DATABASE_URL=" not in existing:
        password = secrets.token_urlsafe(24)
        pg.change_password(app.database_user, password)
        return existing + _database_url(scheme, app, password)
    return _replace_database_url_scheme(existing, source)


def _database_url_scheme(source: Path) -> str:
    return str(
        app_project_detector.detect_project(source).get(
            "database_url_scheme", "postgresql"
        )
    )


def _replace_database_url_scheme(existing: str, source: Path) -> str:
    return re.sub(
        r"(?m)^DATABASE_URL=postgresql(?:\+[A-Za-z0-9_]+)?://",
        f"DATABASE_URL={_database_url_scheme(source)}://",
        existing,
    )


def database_url_with_scheme(url: str, scheme: str) -> str:
    """Apply the detected SQLAlchemy driver before a protected env is saved."""
    return re.sub(
        r"^postgresql(?:\+[A-Za-z0-9_]+)?://", f"{scheme}://", url
    )


def _with_runtime_settings(app: HostedApp, existing: str) -> str:
    """Keep service bindings after a deployment restores user environment values."""
    existing = "".join(
        f"{line}\n" for line in existing.splitlines()
        if not line.startswith(("HOST=", "PORT=", "APP_DATA_DIR="))
    )
    data_dir = Path(app.work_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return (
        existing
        + f"HOST=127.0.0.1\nPORT={app.port}\nAPP_DATA_DIR={data_dir}\n"
    )


def _database_url(scheme: str, app: HostedApp, password: str) -> str:
    return (
        f"DATABASE_URL={scheme}://{app.database_user}:{password}"
        f"@127.0.0.1:5432/{app.database_name}\n"
    )


async def _progress(reporter, stage: str, message: str) -> None:
    if reporter:
        await reporter(stage, message)


def _tail(value: str, fallback: str) -> str:
    return (value or fallback)[-1000:]
