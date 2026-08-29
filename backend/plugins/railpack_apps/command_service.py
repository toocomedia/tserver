"""Service for discovering app containers, suggested commands, and executing commands inside containers."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.container_app import ContainerApp
from models.domain import Domain
from services import container_app_service as apps
from services.apps_engine.runtime_dispatch import is_compose_app


def get_authorized_containers(app: ContainerApp) -> List[Dict[str, Any]]:
    """Returns a list of containers authorized for command execution for this app."""
    containers: List[Dict[str, Any]] = []

    if is_compose_app(app):
        # Multi-service official stack
        service_names: List[str] = []
        raw = getattr(app, "stack_services", None)
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, dict) and "services" in data:
                    service_names = list(data["services"].keys())
            except (TypeError, ValueError):
                pass

        if not service_names:
            try:
                from services.official_stacks.compose_runtime import stack_from_runtime
                stack = stack_from_runtime(app)
                service_names = list(stack.services.keys())
            except Exception:
                pass

        if not service_names and app.stack_catalog_id:
            service_names = [app.stack_catalog_id]

        primary_service = service_names[0] if service_names else "web"
        try:
            from services.official_stacks.compose_runtime import stack_from_runtime
            primary_service = stack_from_runtime(app).web_service_name
        except Exception:
            pass

        # Put primary service first
        ordered_services = []
        if primary_service in service_names:
            ordered_services.append(primary_service)
        for s in service_names:
            if s not in ordered_services:
                ordered_services.append(s)

        if not ordered_services:
            ordered_services = ["web"]

        for svc in ordered_services:
            cname = f"srv-stack-{app.id}-{svc}"
            is_primary = (svc == primary_service)
            containers.append({
                "name": cname,
                "service": svc,
                "is_primary": is_primary,
                "label": f"{svc} ({cname})" if not is_primary else f"{svc} [Primary Web] ({cname})",
            })
    else:
        # Standard Railpack or custom container app
        cname = app.container_name or f"srv-app-{app.id}"
        containers.append({
            "name": cname,
            "service": "app",
            "is_primary": True,
            "label": f"app ({cname})",
        })

    return containers


SYSTEM_INSPECTION_COMMANDS: List[Dict[str, str]] = [
    {"label": "Directory (ls -la)", "command": "ls -la"},
    {"label": "Working Dir (pwd)", "command": "pwd"},
    {"label": "Environment (env)", "command": "env"},
    {"label": "Disk Space (df -h)", "command": "df -h"},
    {"label": "Processes (ps aux)", "command": "ps aux || ps -ef"},
    {"label": "OS Release", "command": "cat /etc/os-release 2>/dev/null || cat /etc/issue"},
    {"label": "Whoami", "command": "whoami"},
]

# Declarative registry of framework/runtime quick command recipes
COMMAND_RECIPES: List[Dict[str, Any]] = [
    {
        "patterns": ("shynet",),
        "factory": lambda app, text: [
            {"label": "Admin Setup", "command": f"./manage.py registeradmin {(getattr(app, 'wordpress_admin_email', None) or '').strip() or 'admin@example.com'}"},
            {"label": "Whitelabel Name", "command": './manage.py whitelabel "My Analytics"'},
            {"label": "Migrations", "command": "./manage.py showmigrations"},
        ],
    },
    {
        "patterns": ("wordpress",),
        "factory": lambda app, text: [
            {"label": "WP Info", "command": "wp --allow-root --info"},
            {"label": "List Users", "command": "wp --allow-root user list"},
            {"label": "List Plugins", "command": "wp --allow-root plugin list"},
            {"label": "List Themes", "command": "wp --allow-root theme list"},
        ],
    },
    {
        "patterns": ("django",),
        "factory": lambda app, text: [
            {"label": "Django Check", "command": "python manage.py check"},
            {"label": "Show Migrations", "command": "python manage.py showmigrations"},
            {"label": "Run Migrations", "command": "python manage.py migrate"},
            {"label": "Python Version", "command": "python --version"},
            {"label": "Installed Packages", "command": "pip list"},
        ],
    },
    {
        "patterns": ("python",),
        "predicate": lambda app, text: getattr(app, "build_mode", None) == "railpack",
        "factory": lambda app, text: [
            {"label": "Python Version", "command": "python --version"},
            {"label": "Installed Packages", "command": "pip list"},
        ],
    },
    {
        "patterns": ("laravel", "php"),
        "factory": lambda app, text: [
            {"label": "Artisan Info", "command": "php artisan --version"},
            {"label": "Route List", "command": "php artisan route:list"},
            {"label": "Migration Status", "command": "php artisan migrate:status"},
            {"label": "Config Cache", "command": "php artisan config:cache"},
        ],
    },
    {
        "patterns": ("strapi",),
        "factory": lambda app, text: [
            {"label": "Node Version", "command": "node -v"},
            {"label": "NPM Packages", "command": "npm list --depth=0"},
            {"label": "Strapi Admin User", "command": "npm run strapi admin:create-user"},
        ],
    },
    {
        "patterns": ("node", "npm"),
        "factory": lambda app, text: [
            {"label": "Node Version", "command": "node -v"},
            {"label": "NPM Packages", "command": "npm list --depth=0"},
        ],
    },
]


def get_quick_commands(app: ContainerApp, domain: Optional[Domain] = None) -> List[Dict[str, str]]:
    """Generates suggested quick commands tailored for the app's framework/runtime."""
    image_ref = (getattr(app, "image_reference", None) or "").lower()
    repo_url = (getattr(app, "repository_url", None) or "").lower()
    catalog_id = (getattr(app, "stack_catalog_id", None) or "").lower()
    preset = (getattr(app, "preset", None) or "").lower()
    app_text = f"{image_ref} {repo_url} {catalog_id} {preset}".lower()

    quick_cmds: List[Dict[str, str]] = []

    for recipe in COMMAND_RECIPES:
        patterns = recipe.get("patterns", ())
        predicate = recipe.get("predicate")
        if (predicate and predicate(app, app_text)) or any(p in app_text for p in patterns):
            factory = recipe.get("factory")
            if factory:
                quick_cmds.extend(factory(app, app_text))
            break

    # Standard system inspection commands
    quick_cmds.extend(SYSTEM_INSPECTION_COMMANDS)

    return quick_cmds


async def execute_app_command(
    app: ContainerApp,
    command: str,
    container_name: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Executes a shell command inside an authorized container for this application.
    Returns standard execution output, timing, and exit status.
    """
    cmd_str = (command or "").strip()
    if not cmd_str:
        raise HTTPException(400, "Command cannot be empty.")

    if len(cmd_str) > 2000:
        raise HTTPException(400, "Command length cannot exceed 2000 characters.")

    if app.status in {"deleting", "delete_failed", "data_preserved"}:
        raise HTTPException(409, "Commands cannot be run while the app is in a deleted or deleting state.")

    if app.status != "running":
        raise HTTPException(400, f"Cannot run command: Application container is not running (Current status: {app.status}).")

    authorized = get_authorized_containers(app)
    authorized_names = {item["name"] for item in authorized}

    target = container_name.strip() if container_name else ""
    if not target:
        target = authorized[0]["name"] if authorized else (app.container_name or f"srv-app-{app.id}")

    if target not in authorized_names:
        raise HTTPException(403, f"Target container '{target}' does not belong to this application.")

    # Timeout clamped between 5 and 60 seconds
    clamped_timeout = max(5, min(int(timeout), 60))

    exec_args = ["docker", "exec", "-i", target, "sh", "-c", cmd_str]

    start_time = time.perf_counter()
    try:
        proc_result = await asyncio.to_thread(apps._run, exec_args, timeout=clamped_timeout)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "success": False,
            "command": cmd_str,
            "container": target,
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Execution error: {exc}",
            "duration_ms": duration_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    duration_ms = int((time.perf_counter() - start_time) * 1000)

    # Trim excessively large output to prevent payload bloat (max 100 KB)
    max_bytes = 100_000
    stdout_trimmed = (proc_result.stdout or "")[:max_bytes]
    stderr_trimmed = (proc_result.stderr or "")[:max_bytes]

    return {
        "success": (proc_result.returncode == 0),
        "command": cmd_str,
        "container": target,
        "exit_code": proc_result.returncode,
        "stdout": stdout_trimmed,
        "stderr": stderr_trimmed,
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
