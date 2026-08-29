"""services/documentation_service.py — Compiles structured app documentation and runbook commands."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from models.container_app import ContainerApp
from models.container_app_snapshot import ContainerAppSnapshot
from models.domain import Domain
from services.apps_engine.runtime_dispatch import is_compose_app


DOCUMENTATION_RECIPES: List[Dict[str, Any]] = [
    {
        "patterns": ("shynet",),
        "admin_commands": lambda target, email, app: [
            {
                "title": "Create Administrator Account (Superuser)",
                "command": f"docker exec -it {target} ./manage.py registeradmin {email}",
                "description": "Initializes the superuser account in PostgreSQL and prints your initial temporary password.",
            },
            {
                "title": "Set Whitelabel Instance Name",
                "command": f'docker exec -it {target} ./manage.py whitelabel "My Analytics"',
                "description": "Customizes the display name and branding of your Shynet instance.",
            },
        ],
    },
    {
        "patterns": ("wordpress",),
        "admin_commands": lambda target, email, app: [
            {
                "title": "List WordPress Users (WP-CLI)",
                "command": f"docker exec -it {target} wp --allow-root user list",
                "description": "Lists all registered users in the WordPress database.",
            },
            {
                "title": "Reset WordPress Password",
                "command": f"docker exec -it {target} wp --allow-root user update {getattr(app, 'wordpress_admin_user', None) or 'admin'} --user_pass='NEW_PASSWORD'",
                "description": "Updates the password for the primary administrator account.",
            },
        ],
    },
    {
        "patterns": ("django", "python"),
        "admin_commands": lambda target, email, app: [
            {
                "title": "Create Django Superuser",
                "command": f"docker exec -it {target} python manage.py createsuperuser",
                "description": "Launches the interactive Django superuser creation wizard.",
            },
        ],
    },
    {
        "patterns": ("laravel", "php"),
        "admin_commands": lambda target, email, app: [
            {
                "title": "Run Database Migrations",
                "command": f"docker exec -it {target} php artisan migrate --force",
                "description": "Applies all pending database migrations in the container.",
            },
        ],
    },
    {
        "patterns": ("strapi",),
        "admin_commands": lambda target, email, app: [
            {
                "title": "Create Strapi Administrator",
                "command": f"docker exec -it {target} npm run strapi admin:create-user",
                "description": "Initializes the root Strapi administration account.",
            },
        ],
    },
    {
        "patterns": ("umami",),
        "setup_notes": lambda domain_url: [
            "Default Administrator: Username `admin` | Password `umami` (Change immediately after first login)."
        ],
    },
    {
        "patterns": ("plausible", "n8n"),
        "setup_notes": lambda domain_url: [
            f"Initial Account Setup: Navigate to {domain_url} in your browser to register your owner profile on first visit."
        ],
    },
]


def get_app_documentation(
    app: ContainerApp,
    domain: Optional[Domain] = None,
    active_snapshot: Optional[ContainerAppSnapshot] = None,
) -> Dict[str, Any]:
    """Generates structured documentation and interactive CLI runbook commands for an App Engine app."""
    # Determine the primary container name generically
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
    preset = (getattr(app, "preset", None) or "").lower()
    app_text = f"{image_ref} {repo_url} {catalog_id} {preset}".lower()

    admin_commands: List[Dict[str, str]] = []
    maintenance_commands: List[Dict[str, str]] = []
    setup_notes: List[str] = []

    # 1. Match Framework / Stack Recipes Declaratively
    for recipe in DOCUMENTATION_RECIPES:
        patterns = recipe.get("patterns", ())
        if any(p in app_text for p in patterns):
            admin_builder = recipe.get("admin_commands")
            if admin_builder:
                admin_commands.extend(admin_builder(target_container, admin_email, app))
            notes_builder = recipe.get("setup_notes")
            if notes_builder:
                setup_notes.extend(notes_builder(domain_url))
            break

    # 2. General Container Maintenance Commands
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
