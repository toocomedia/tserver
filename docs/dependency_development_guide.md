# SRV Panel Dependency Development Guide

System dependencies are trusted host capabilities used by feature plugins. They live under `backend/dependencies/`, have a separate `/dependencies` page, and cannot be installed from plugin ZIP archives.

## Adding a Dependency

1. Add the dependency ID and driver class to `dependencies/registry.py`. The registry is the closed security boundary; never auto-scan drivers.
2. Add `<id>/dependency.json` with matching `id`, display name, description, driver version, and core author.
3. Implement a driver exposing `get_status(force=False)`, `toggle(enabled)`, `get_install_guide()`, `get_uninstall_guide()`, and `list_containers()` or the equivalent ownership inventory for that technology.
4. Add mocked Python tests for detection, timeout, cache, enable/disable verification, precheck inventory, and error rollback.
5. Document operator recovery and data-preservation behavior.

Dependency IDs use lowercase letters/numbers with optional underscores or hyphens. A plugin references them through `requires.dependencies`. Unknown IDs block that plugin.

## Driver Requirements

- Status must distinguish installed, running, healthy, stopped, and error states.
- Health checks must be cached and have strict timeouts. Docker uses a five-second cache and two-second probe timeout.
- State-changing commands use argument arrays with `shell=False`; never interpolate request values into shell commands.
- Enable/disable must verify the observed result before reporting success.
- The manager serializes operations per dependency and rolls desired state back on failure.
- Mutable desired state and last errors live in `component_states`; manifests remain declarative.
- Installation may be automated only through a fixed core-owned script with an exact sudoers entry, no request-controlled arguments, operation locking, timeouts, and post-install health verification. Package removal remains guided.

## UI and API Contract

Every dependency appears on `/dependencies`. Generic APIs provide status, prechecks, toggle, and install/uninstall guides. POST operations are authenticated, CSRF-protected, and repeat prechecks server-side. A disable operation requires explicit confirmation even when no dependent plugin is currently active.

The dependency manager queries plugin manifests to list dependents. Dependency loss pauses them without changing their desired state. Recovery makes them active again only when their own desired state remains enabled.

## Testing

Use `unittest`, temporary directories/databases, and `unittest.mock`. Tests must not invoke the host package manager, systemd, Docker daemon, network, or Uvicorn.
