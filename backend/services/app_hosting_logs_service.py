"""Read-only application service logs for hosted Python apps."""
from fastapi import HTTPException

from models.hosted_app import HostedApp
from utils import shell


async def get_logs(app: HostedApp) -> str:
    result = await shell.run(
        ["journalctl", "-u", app.service_name, "-n", "200", "--no-pager"],
        timeout=20,
    )
    if not result.success:
        raise HTTPException(500, result.stderr or "Could not read application logs.")
    return result.stdout or "No service logs have been written yet."


def update_commands(app: HostedApp, build_command: str, start_command: str) -> None:
    build_command, start_command = build_command.strip(), start_command.strip()
    if not build_command or not start_command:
        raise HTTPException(400, "Build and start commands are required.")
    if len(build_command) > 1000 or len(start_command) > 1000:
        raise HTTPException(400, "Commands must be 1000 characters or fewer.")
    app.build_command = build_command
    app.start_command = start_command
