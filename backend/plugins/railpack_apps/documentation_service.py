"""services/documentation_service.py — Compiles structured app documentation and runbook commands."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from models.container_app import ContainerApp
from models.container_app_snapshot import ContainerAppSnapshot
from models.domain import Domain
from services.apps_engine.runtime_dispatch import is_compose_app


def get_app_documentation(
    app: ContainerApp,
    domain: Optional[Domain] = None,
    active_snapshot: Optional[ContainerAppSnapshot] = None,
) -> Dict[str, Any]:
    """Generates structured documentation and interactive CLI runbook commands for an App Engine app."""
    if is_compose_app(app):
        from plugins.railpack_apps import command_service
        containers = command_service.get_authorized_containers(app)
        primary = next((c for c in containers if c.get("is_primary")), containers[0] if containers else None)
        target_container = primary["name"] if primary else f"srv-stack-{app.id}-web"
    else:
        target_container = getattr(app, "container_name", None) or f"srv-app-{app.id}"

    admin_email = (getattr(app, "wordpress_admin_email", None) or "").strip() or "admin@example.com"
    domain_name = domain.name if domain else "your-domain.com"
    domain_url = f"https://{domain_name}"

    image_ref = (getattr(app, "image_reference", None) or "").lower()
    repo_url = (getattr(app, "repository_url", None) or "").lower()
    catalog_id = (getattr(app, "stack_catalog_id", None) or "").lower()
    app_text = f"{image_ref} {repo_url} {catalog_id}".lower()

    admin_commands: List[Dict[str, str]] = []
    maintenance_commands: List[Dict[str, str]] = []
    setup_notes: List[str] = []

    # 1. Pull dynamic evidence from active snapshot configuration if present
    if active_snapshot and active_snapshot.config_json:
        try:
            cfg = json.loads(active_snapshot.config_json)
            if isinstance(cfg, dict):
                for note in cfg.get("setup_notes") or []:
                    if note and note not in setup_notes:
                        setup_notes.append(str(note))
                for cmd in cfg.get("admin_commands") or []:
                    if isinstance(cmd, dict) and cmd.get("command"):
                        admin_commands.append(cmd)
        except Exception:
            pass

    # 2. Dynamic CLI administrator command for apps with manage.py (e.g. Shynet)
    if "shynet" in app_text and not any("registeradmin" in c.get("command", "") for c in admin_commands):
        admin_commands.append({
            "title": "Create Administrator Account (Superuser)",
            "command": f"docker exec -it {target_container} ./manage.py registeradmin {admin_email}",
            "description": "Initializes the superuser account in PostgreSQL and prints your initial temporary password.",
        })

    # 3. Universal Container Maintenance Commands
    for title, cmd, desc in (
        ("Follow Live Application Logs", f"docker logs -f --tail 100 {target_container}", "Streams realtime standard output and error logs from the container."),
        ("Open Interactive Container Shell", f"docker exec -it {target_container} sh", "Spawns a direct shell environment inside the container for debugging."),
        ("Inspect Realtime Resource Usage", f"docker stats {target_container} --no-stream", "Displays current CPU, Memory, and I/O utilization."),
        ("Restart Container Service", f"docker restart {target_container}", "Performs a clean restart of the running application container."),
    ):
        maintenance_commands.append({"title": title, "command": cmd, "description": desc})

    return {
        "target_container": target_container,
        "admin_email": admin_email,
        "domain_url": domain_url,
        "admin_commands": admin_commands,
        "maintenance_commands": maintenance_commands,
        "setup_notes": setup_notes,
    }
