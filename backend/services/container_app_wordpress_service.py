"""WordPress preset setup and maintenance through short-lived wp-cli containers."""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

import config
from models.container_app import ContainerApp
from models.domain import Domain
from services import container_app_service

WP_IMAGE = "wordpress:php8.3-apache"
WP_CLI_IMAGE = "wordpress:cli"


def prepare(app: ContainerApp, title: str, username: str, email: str, password: str) -> None:
    validate_setup(title, username, email, password)
    app.preset = "wordpress"
    app.wordpress_content_volume = f"srv-container-wp-content-{app.id}"
    app.wordpress_site_title = title.strip()[:255]
    app.wordpress_admin_user, app.wordpress_admin_email = username, email.strip()[:255]
    volume = container_app_service._run(["docker", "volume", "create", "--label", "srv-panel.plugin=railpack_apps", "--label", f"srv-panel.app-id={app.id}", app.wordpress_content_volume], timeout=30)
    if volume.returncode:
        raise HTTPException(502, (volume.stderr or volume.stdout or "Could not create WordPress content volume.")[-800:])
    secret = Path(config.CONTAINER_APP_ENV_ROOT) / "wordpress" / f"{app.id}.env"
    container_app_service.write_env(secret, {"WORDPRESS_ADMIN_PASSWORD": password})
    app.wordpress_pending_secret_path = str(secret)


def validate_setup(title: str, username: str, email: str, password: str) -> None:
    if not title.strip() or not username.isidentifier() or "@" not in email or len(password) < 12:
        raise HTTPException(400, "Enter a site title, valid administrator details, and a password of at least 12 characters.")


def install_if_pending(app: ContainerApp, domain: Domain) -> None:
    secret = Path(app.wordpress_pending_secret_path or "")
    if not secret.is_file():
        return
    values = dict(line.split("=", 1) for line in secret.read_text(encoding="utf-8").splitlines() if "=" in line)
    command = _cli(app, ["wp", "core", "is-installed"])
    existing = container_app_service._run(command, timeout=90)
    if existing.returncode:
        install = _cli(app, ["wp", "core", "install", f"--url=https://{domain.name}", f"--title={app.wordpress_site_title}", f"--admin_user={app.wordpress_admin_user}", f"--admin_password={values['WORDPRESS_ADMIN_PASSWORD']}", f"--admin_email={app.wordpress_admin_email}", "--skip-email"])
        result = container_app_service._run(install, timeout=180)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "WordPress installation failed.")[-1200:])
    secret.unlink(missing_ok=True)
    app.wordpress_pending_secret_path = None


def update(app: ContainerApp) -> None:
    for command in (["wp", "core", "update"], ["wp", "core", "update-db"], ["wp", "plugin", "update", "--all"], ["wp", "theme", "update", "--all"]):
        result = container_app_service._run(_cli(app, command), timeout=300)
        if result.returncode:
            raise HTTPException(502, (result.stderr or result.stdout or "WordPress update failed.")[-1200:])


def _cli(app: ContainerApp, command: list[str]) -> list[str]:
    return ["docker", "run", "--rm", "--network", container_app_service.network_name(app.id), "--add-host", "host.docker.internal:host-gateway", "--volumes-from", app.container_name, "--env-file", app.env_path, WP_CLI_IMAGE, *command]
