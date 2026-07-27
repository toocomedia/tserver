"""Verified lifecycle controls for hosted Python app systemd units."""
from fastapi import HTTPException

from models.hosted_app import HostedApp
from services import app_ownership_service
from utils import shell


async def control(app: HostedApp, action: str) -> None:
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "Invalid app action.")
    if action != "stop":
        app_ownership_service.apply_identity(app)
        app_ownership_service.assert_unit_owner(app)
        app_ownership_service.require_environment(app)
        if action == "start":
            app_ownership_service.require_port_free(app.port)
    command = ["systemctl", "disable", "--now", app.service_name] if action == "stop" else ["systemctl", "enable", "--now", app.service_name] if action == "start" else ["systemctl", "restart", app.service_name]
    result = await shell.run(command, timeout=30)
    if not result.success:
        raise HTTPException(500, result.stderr or result.stdout or "System service action failed.")
    state = await shell.run(["systemctl", "is-active", app.service_name], timeout=15)
    active = state.stdout.strip() in {"active", "activating", "reloading"}
    if action == "stop" and active:
        raise HTTPException(500, "The app service is still active after stopping it.")
    if action != "stop" and not active:
        raise HTTPException(500, "The app service did not become active. Open Service logs.")
    app.status = "stopped" if action == "stop" else "running"
