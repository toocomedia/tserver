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
    # Determine the primary container name to run commands in
    if is_compose_app(app):
        service_name = "web"
        try:
            from services.official_stacks.compose_runtime import stack_from_runtime
            service_name = stack_from_runtime(app).web_service_name
        except Exception:
            pass
        target_container = f"srv-stack-{app.id}-{service_name}"
        if app.image_reference and "shynet" in app.image_reference.lower():
            target_container = f"srv-stack-{app.id}-shynet"
    else:
        target_container = app.container_name or f"srv-app-{app.id}"

    admin_email = (app.wordpress_admin_email or "").strip() or "admin@example.com"
    domain_name = domain.name if domain else "your-domain.com"
    domain_url = f"https://{domain_name}"

    image_ref = (app.image_reference or "").lower()
    repo_url = (app.repository_url or "").lower()
    app_text = f"{image_ref} {repo_url} {app.stack_catalog_id or ''} {app.preset or ''}".lower()

    admin_commands: List[Dict[str, str]] = []
    maintenance_commands: List[Dict[str, str]] = []
    setup_notes: List[str] = []

    # 1. Detect Framework-Specific Administrative Commands
    if "shynet" in app_text:
        admin_commands.append({
            "title": "Create Administrator Account (Superuser)",
            "command": f"docker exec -it {target_container} ./manage.py registeradmin {admin_email}",
            "description": "Initializes the superuser account in PostgreSQL and prints your initial temporary password.",
        })
        admin_commands.append({
            "title": "Set Whitelabel Instance Name",
            "command": f'docker exec -it {target_container} ./manage.py whitelabel "My Analytics"',
            "description": "Customizes the display name and branding of your Shynet instance.",
        })
    elif "wordpress" in app_text:
        admin_commands.append({
            "title": "List WordPress Users (WP-CLI)",
            "command": f"docker exec -it {target_container} wp --allow-root user list",
            "description": "Lists all registered users in the WordPress database.",
        })
        admin_commands.append({
            "title": "Reset WordPress Password",
            "command": f"docker exec -it {target_container} wp --allow-root user update {app.wordpress_admin_user or 'admin'} --user_pass='NEW_PASSWORD'",
            "description": "Updates the password for the primary administrator account.",
        })
    elif "django" in app_text or "python" in app_text:
        admin_commands.append({
            "title": "Create Django Superuser",
            "command": f"docker exec -it {target_container} python manage.py createsuperuser",
            "description": "Launches the interactive Django superuser creation wizard.",
        })
    elif "laravel" in app_text or "php" in app_text:
        admin_commands.append({
            "title": "Run Database Migrations",
            "command": f"docker exec -it {target_container} php artisan migrate --force",
            "description": "Applies all pending database migrations in the container.",
        })
    elif "strapi" in app_text:
        admin_commands.append({
            "title": "Create Strapi Administrator",
            "command": f"docker exec -it {target_container} npm run strapi admin:create-user",
            "description": "Initializes the root Strapi administration account.",
        })
    elif "umami" in app_text:
        setup_notes.append("Default Administrator: Username `admin` | Password `umami` (Change immediately after first login).")
    elif "plausible" in app_text or "n8n" in app_text:
        setup_notes.append(f"Initial Account Setup: Navigate to {domain_url} in your browser to register your owner profile on first visit.")

    # 2. General Container Maintenance Commands
    maintenance_commands.append({
        "title": "Follow Live Application Logs",
        "command": f"docker logs -f --tail 100 {target_container}",
        "description": "Streams realtime standard output and error logs from the container.",
    })
    maintenance_commands.append({
        "title": "Open Interactive Container Shell",
        "command": f"docker exec -it {target_container} sh",
        "description": "Spawns a direct shell environment inside the container for debugging.",
    })
    maintenance_commands.append({
        "title": "Inspect Realtime Resource Usage",
        "command": f"docker stats {target_container} --no-stream",
        "description": "Displays current CPU, Memory, and I/O utilization.",
    })
    maintenance_commands.append({
        "title": "Restart Container Service",
        "command": f"docker restart {target_container}",
        "description": "Performs a clean restart of the running application container.",
    })

    return {
        "target_container": target_container,
        "admin_email": admin_email,
        "domain_url": domain_url,
        "admin_commands": admin_commands,
        "maintenance_commands": maintenance_commands,
        "setup_notes": setup_notes,
    }
