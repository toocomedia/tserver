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
                        cmd_copy = dict(cmd)
                        cmd_copy["command"] = cmd_copy["command"].replace("{target}", target_container).replace("{container}", target_container).replace("{admin_email}", admin_email)
                        admin_commands.append(cmd_copy)
                    elif isinstance(cmd, str) and cmd.strip():
                        cmd_str = cmd.strip().replace("{target}", target_container).replace("{container}", target_container).replace("{admin_email}", admin_email)
                        if not cmd_str.startswith("docker exec"):
                            cmd_str = f"docker exec -it {target_container} {cmd_str}"
                        admin_commands.append({
                            "title": "Administrator Command",
                            "command": cmd_str,
                            "description": "Administrative bootstrap command discovered in application documentation.",
                        })

                post_install = cfg.get("post_install_message") or (cfg.get("app_spec") or {}).get("post_install_message")
                if post_install and str(post_install).strip():
                    post_text = str(post_install).strip()
                    if post_text not in setup_notes:
                        setup_notes.append(post_text)
                    import re
                    match = re.search(r"(docker exec[^\n]+|(?:\./manage\.py|python manage\.py|artisan|wp)[^\n]+)", post_text)
                    if match:
                        raw_cmd = match.group(1).strip().replace("{target}", target_container).replace("{container}", target_container).replace("{admin_email}", admin_email)
                        if not raw_cmd.startswith("docker exec"):
                            raw_cmd = f"docker exec -it {target_container} {raw_cmd}"
                        if not any(c.get("command") == raw_cmd for c in admin_commands):
                            admin_commands.append({
                                "title": "Initial Administrator Setup",
                                "command": raw_cmd,
                                "description": "Initializes administrative credentials according to application documentation.",
                            })
        except Exception:
            pass

    # 2. Fallback to command_service quick commands if snapshot lacked setup instructions
    if not admin_commands:
        try:
            from plugins.railpack_apps import command_service
            quick_cmds = command_service.get_quick_commands(app)
            for qc in quick_cmds:
                lbl = qc.get("label", "")
                raw = qc.get("command", "")
                if any(k in lbl.lower() for k in ("admin", "superuser", "setup")) or any(k in raw.lower() for k in ("registeradmin", "createsuperuser")):
                    cmd_str = f"docker exec -it {target_container} {raw}"
                    admin_commands.append({
                        "title": lbl or "Initial Administrator Setup",
                        "command": cmd_str,
                        "description": "Initial administrative setup command.",
                    })
        except Exception:
            pass

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
