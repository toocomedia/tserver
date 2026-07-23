# Final Implementation Plan — Docker Dependency System

## Goal

Keep feature plugins under `/plugins` and trusted host dependencies under `/dependencies`. Docker Engine is the first closed, core-owned dependency. Plugins can require Docker and are automatically paused whenever it is disabled, stopped, unhealthy, or absent.

## Architecture

- `backend/dependencies/registry.py` is the only accepted dependency registry. It is hardcoded and never populated by uploads or directory scanning.
- Each registered driver supplies metadata, cached health/status, guarded enable/disable, guided install/uninstall, and an ownership inventory for destructive prechecks.
- Plugin manifests may declare `requires.dependencies`. Unknown IDs block the plugin with a visible error.
- Mutable desired state, lifecycle operation, install origin, and last error are persisted in SQLite `component_states`; manifests provide first-discovery defaults only.
- Plugin routers remain mounted but every endpoint receives a core availability dependency. HTML receives a warning page and API calls receive structured `409`/`503` errors.
- Background plugin work must check `effective_status == active` before running.

## Lifecycle Rules

- Effective plugin state is derived from manifest validity, installation detection, desired enabled state, lifecycle operation, and dependency health.
- Dependency loss never changes a plugin's desired state. Recovery resumes only plugins still desired-enabled.
- Enable/disable and install/uninstall operations are locked per component, run once with timeouts, verify results, and retain the previous desired state on failure.
- Docker disable repeats its precheck server-side, requires confirmation, disables/stops both the socket and service, and verifies daemon shutdown.
- Docker installation uses a fixed core-owned, sudoers-allowlisted Ubuntu script and verifies daemon health. Package removal remains guided. `/var/lib/docker` is never removed by the panel.
- Docker plugins label resources `srv-panel.plugin=<plugin-id>`. Uninstall removes only owned runtime resources and preserves volumes/data unless a separate purge is explicitly confirmed.

## Security

- Plugin ZIPs extract into temporary staging with path containment, file-count/size limits, symlink rejection, one-root/one-manifest validation, reserved ID/type rejection, and atomic move into place.
- Uploaded plugins cannot contain dependency manifests, claim a system type, overwrite existing/core plugins, or reference unknown dependencies.
- Commands use argument arrays, `shell=False`, fixed allowlisted actions, non-interactive sudo when configured, and explicit timeouts.

## Public Interfaces

- `GET /dependencies`
- `GET /api/dependencies/status`
- `GET /api/dependencies/{id}/precheck?action=disable|uninstall`
- `POST /api/dependencies/{id}/toggle`
- `POST /api/dependencies/{id}/install`
- `GET /api/dependencies/{id}/install-guide`
- `GET /api/dependencies/{id}/uninstall-guide`

Plugin manifest dependency declaration:

```json
{
  "requires": {
    "dependencies": ["docker"]
  }
}
```

## Validation

Validation is Python-only and must not start Uvicorn, Docker, systemd, or any application stack:

```bash
python -m compileall -q backend
python -m unittest discover -s backend/tests -p "test_*.py"
```

Tests cover persisted desired state, cached Docker health/timeouts, dependency pause/recovery behavior, manual disable precedence, guarded direct routes, lifecycle rollback/locking, and malicious ZIP archives.

## Documentation

- `docs/plugin_development_guide.md`: plugin manifests, runtime guards, lifecycle hooks, ownership labels, packaging, and tests.
- `docs/dependency_development_guide.md`: closed registry and future driver contract.
- `docs/docker_dependency_operations.md`: operator states, guides, recovery, and data safety.
