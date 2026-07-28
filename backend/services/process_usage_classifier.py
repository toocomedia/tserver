"""Exclusive stack-service classification for Usage page processes."""
from pathlib import Path

import config


def stack_service(name: str, cmdline: str) -> str | None:
    name = (name or "").lower()
    command = (cmdline or "").lower().replace("\\", "/")
    if "nginx" in name:
        return "nginx"
    if "pdns_server" in name:
        return "powerdns"
    if name in {"dockerd", "containerd", "docker-proxy"}:
        return "docker"
    if "python" not in name and "uvicorn" not in name:
        return None
    app_root = str(Path(config.APP_HOSTING_ROOT)).lower().replace("\\", "/").rstrip("/")
    if app_root and f"{app_root}/" in command:
        return None
    return "panel" if "srv-panel" in command else None


def is_nginx_worker(cmdline: str) -> bool:
    """Identify an Nginx worker from its Linux process title."""
    return "nginx: worker process" in (cmdline or "").lower()
