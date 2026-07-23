# SRV-Panel Plugin Development Guide

This document explains how the **SRV Panel Plugin Architecture** works and how to create, package, test, and safely remove feature plugins. Core system dependencies such as Docker are not plugins and cannot be uploaded.

---

## 1. Overview & Architecture

SRV-Panel features a **zero-touch plugin auto-discovery system**. All plugins reside isolated in the `backend/plugins/` directory:

```
backend/plugins/
├── manager.py                   # Core Plugin Loader & Registry
└── my_custom_plugin/            # Your Plugin Folder
    ├── plugin.json              # Plugin Manifest (Required)
    ├── router.py                # FastAPI APIRouter (Optional)
    ├── service.py               # Background Logic & Business Code (Optional)
    ├── templates/               # Custom Jinja HTML Pages (Optional)
    │   └── custom_page.html
    └── scripts/                 # Bash Install & Uninstall Scripts (Optional)
        ├── install.sh
        └── uninstall.sh
```

---

## 2. The Plugin Manifest (`plugin.json`)

Every plugin **must** include a `plugin.json` file in its root directory:

```json
{
  "id": "my_custom_plugin",
  "name": "My Custom Plugin",
  "description": "Short description of what this plugin does.",
  "version": "1.0.0",
  "author": "Your Name / Organization",
  "icon": "mail",
  "route_prefix": "/plugins/my_custom_plugin",
  "sidebar": true,
  "sidebar_label": "Custom Plugin",
  "enabled": true,
  "requires": {
    "dependencies": ["docker"]
  },
  "install_script": "scripts/install.sh",
  "uninstall_script": "scripts/uninstall.sh"
}
```

### Manifest Fields:
* `id` *(string, required)*: Unique identifier (must match folder name).
* `name` *(string, required)*: Display name shown in Plugins Manager.
* `description` *(string)*: Brief overview of plugin features.
* `version` *(string)*: Semantic versioning string (e.g. `1.0.0`).
* `icon` *(string)*: Icon identifier for sidebar (`mail`, `grid`, etc.).
* `route_prefix` *(string)*: Entrypoint URL path (e.g. `/plugins/my_custom_plugin`).
* `sidebar` *(boolean)*: Set `true` to render a navigation item in the panel sidebar.
* `sidebar_label` *(string)*: Label displayed in the sidebar navigation link.
* `install_script` *(string)*: Path to bash script executed when installing the plugin.
* `uninstall_script` *(string)*: Path to bash script executed when uninstalling the plugin.
* `requires.dependencies` *(list[string], optional)*: Trusted system dependencies required at runtime. Currently the only accepted ID is `docker`.

`enabled` supplies the default only when the plugin is first discovered. After that, the administrator's desired state is stored in the panel database and is not rewritten into the manifest.

Unknown dependency IDs block the plugin and appear as a clear manifest error. They never cause an uploaded plugin to become a system dependency.

---

## 3. Creating Plugin Routes (`router.py`)

Create a standard FastAPI `APIRouter` in `router.py`. The `PluginManager` will auto-mount `router` onto the main application on startup:

```python
# backend/plugins/my_custom_plugin/router.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from templating import templates

router = APIRouter(prefix="/plugins/my_custom_plugin", tags=["my_custom_plugin"])

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("custom_page.html", {
        "request": request,
        "active_page": "plugins",
    })
```

---

## 4. Custom Jinja Templates (`templates/`)

Place any HTML templates inside `templates/`. The `PluginManager` automatically appends this directory to Jinja's template search path.

Templates can extend the panel's global base layout:

```html
{% extends "layout.html" %}

{% block title %}My Custom Plugin — SRV Panel{% endblock %}
{% block page_title %}My Custom Plugin{% endblock %}

{% block content %}
<div class="card p-5">
  <h2>Welcome to My Plugin</h2>
  <p>This is a custom plugin page extending layout.html!</p>
</div>
{% endblock %}
```

---

## 5. Packaging & Distributing Plugins

To distribute your plugin to other SRV-Panel users:

1. Compress your plugin directory into a `.zip` archive:
   ```bash
   zip -r my_custom_plugin.zip my_custom_plugin/
   ```
2. Upload the `.zip` archive via the **Plugins Manager** page (`/plugins/`) in the SRV-Panel Web UI.
3. The plugin will be automatically extracted, validated, and registered!

---

## 6. One-Click Clean Uninstallation Best Practices

To adhere to SRV-Panel's low-RAM philosophy, ensure your `uninstall.sh` script cleanly stops background processes, deletes installed binaries, and releases system memory:

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Stop background service
systemctl stop my_custom_service || true
systemctl disable my_custom_service || true

# 2. Remove binary and service unit
rm -f /usr/local/bin/my_custom_binary
rm -f /etc/systemd/system/my_custom_service.service

systemctl daemon-reload
echo "==> Uninstalled cleanly!"
```

The panel runs each lifecycle script once with a timeout. It does not retry a failed script automatically. Scripts must therefore be idempotent and return non-zero when cleanup is incomplete.

## 7. Runtime Pause and Resume Contract

All plugin routes are protected by the core availability guard. A plugin is unavailable when it is manually disabled, not installed, invalid, or missing a required dependency. Direct URLs and API calls cannot bypass this check.

A plugin service may provide optional synchronous lifecycle hooks:

```python
class MyService:
    def pause(self) -> None:
        """Stop owned work without deleting permanent data."""

    def resume(self) -> None:
        """Resume owned work after explicit enable."""

service = MyService()
```

Hooks must finish within 60 seconds and raise an exception on failure. Dependency outages do not change the plugin's desired enabled state. When Docker becomes healthy, a plugin resumes only if the administrator did not manually disable it.

Background jobs do not pass through a web route. Before doing work they must call `plugin_manager.get_plugin(plugin_id)` and continue only when `effective_status == "active"`.

## 8. Docker Resource Ownership

Every Docker resource created by a plugin must include this label:

```text
srv-panel.plugin=<plugin-id>
```

Use a stable Compose project name derived from the plugin ID. Disable stops only owned containers. Before a Docker plugin's uninstall script runs, the core removes containers and networks carrying its ownership label. Volumes and application data are preserved unless a separate purge is explicitly implemented and confirmed. Never use broad commands such as `docker system prune` from a plugin.

## 9. Packaging Security Rules

An uploaded archive must contain exactly one `<plugin-id>/plugin.json` and all files must stay under that folder. Uploads are rejected for unsafe paths, symlinks, excessive size/count, reserved IDs, dependency manifests, system-type claims, unknown dependencies, or attempts to overwrite an installed/core plugin.

Uploaded Python routers and lifecycle scripts execute as trusted administrator-installed code. Only install plugins from trusted sources.

## 10. Python-Only Tests

Tests must mock Docker, systemd, subprocess, and network access. Do not require a running panel stack. Run the repository suite with:

```bash
python -m unittest discover -s backend/tests -p "test_*.py"
```
