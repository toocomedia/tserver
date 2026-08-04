"""
Relationship resolver for Safe Install Mode.

Classifies every panel-managed service into one of three buckets:
  protected  — must never be stopped by Safe Install
  required   — needed by the operation being installed (must not be stopped)
  optional   — eligible candidates (safe_temporary_stop declared + adapter exists)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Core protected services that Safe Install must never stop.
# These are identified by their dependency ID or a well-known label.
# ──────────────────────────────────────────────────────────────────
_ALWAYS_PROTECTED_DEP_IDS = frozenset({
    "docker",
    "nginx",
    "certbot",
})


async def classify_services(
    db: AsyncSession,
    install_operation: dict,
) -> dict[str, list[dict]]:
    """
    Return:
      {
        "protected": [...],   # never stop
        "required":  [...],   # needed by the operation being installed
        "optional":  [...],   # eligible candidates
      }

    Each entry is:
      {
        "id":        str,
        "type":      "plugin" | "dependency" | "container_app",
        "label":     str,
        "reason":    str,       # why it was placed in this bucket
        "ram_mb":    int | None,
        "safe_temporary_stop": bool,
        "has_adapter":         bool,
      }
    """
    from dependencies import dependency_manager
    from plugins import plugin_manager
    from services.component_state import component_state_store

    protected: list[dict] = []
    required: list[dict] = []
    optional: list[dict] = []

    # ── Derive which services are required by the install operation ──
    required_dep_ids: set[str] = set(
        install_operation.get("required_dependencies", [])
    )

    # ── 1. Dependency services ────────────────────────────────────────
    for dep in dependency_manager.get_all_statuses(cached=False):
        dep_id = dep["id"]
        entry = {
            "id": dep_id,
            "type": "dependency",
            "label": dep.get("name", dep_id),
            "ram_mb": None,
            "safe_temporary_stop": False,
            "has_adapter": False,
        }

        if dep_id in _ALWAYS_PROTECTED_DEP_IDS or not dep.get("healthy", False):
            entry["reason"] = "Core system dependency — never stop."
            protected.append(entry)
            continue

        if dep_id in required_dep_ids:
            entry["reason"] = "Required by the operation being installed."
            required.append(entry)
            continue

        # Check if the dependency service has a lifecycle adapter
        svc = dependency_manager.get_service(dep_id)
        has_adapter = (
            svc is not None
            and callable(getattr(svc, "stop", None))
            and callable(getattr(svc, "start", None))
            and callable(getattr(svc, "is_running", None))
        )
        # safe_temporary_stop declared in dependency metadata
        safe_stop = bool(dep.get("safe_temporary_stop", False))

        if safe_stop and has_adapter:
            entry["reason"] = "Optional — declares safe_temporary_stop with a lifecycle adapter."
            entry["safe_temporary_stop"] = True
            entry["has_adapter"] = True
            optional.append(entry)
        else:
            reason_parts = []
            if not safe_stop:
                reason_parts.append("does not declare safe_temporary_stop")
            if not has_adapter:
                reason_parts.append("no panel-owned lifecycle adapter")
            entry["reason"] = "Protected — " + " and ".join(reason_parts) + "."
            protected.append(entry)

    # ── 2. Plugin services ────────────────────────────────────────────
    for plugin in plugin_manager.list_plugins(check_dependencies=False):
        plugin_id = plugin["id"]
        status = plugin.get("effective_status", "")
        if status not in ("active", "enabling", "disabling"):
            continue  # not running — not relevant

        manifest = plugin_manager.plugins.get(plugin_id, {})
        rg = manifest.get("resource_guard", {})
        safe_stop = bool(rg.get("safe_temporary_stop", False))
        adapter_name = rg.get("lifecycle_adapter", "")
        has_adapter = bool(adapter_name)  # further validated in manager.py

        entry = {
            "id": plugin_id,
            "type": "plugin",
            "label": plugin.get("name", plugin_id),
            "ram_mb": None,
            "safe_temporary_stop": safe_stop,
            "has_adapter": has_adapter,
        }

        # Active app dependencies are always protected
        dep_of_active = _is_dependency_of_active_app(plugin_id)
        if dep_of_active:
            entry["reason"] = "Required by an active application — never stop."
            protected.append(entry)
            continue

        if plugin_id in required_dep_ids:
            entry["reason"] = "Required by the operation being installed."
            required.append(entry)
            continue

        if safe_stop and has_adapter:
            entry["reason"] = "Optional — declares safe_temporary_stop with a lifecycle adapter."
            optional.append(entry)
        else:
            parts = []
            if not safe_stop:
                parts.append("does not declare safe_temporary_stop")
            if not has_adapter:
                parts.append("no panel-owned lifecycle adapter")
            entry["reason"] = "Protected — " + " and ".join(parts) + "."
            protected.append(entry)

    return {"protected": protected, "required": required, "optional": optional}


def _is_dependency_of_active_app(service_id: str) -> bool:
    """
    Returns True if *service_id* is listed as a dependency of any currently
    active hosted Python App or Apps Engine app.

    This is a best-effort check using the component_state_store.
    On a live VPS this would also inspect running Docker labels.
    """
    # Placeholder: extend this when active-app dependency tracking is added.
    # For now, always return False (callers will still see safe_temporary_stop
    # validation as the primary guard).
    return False


def get_resource_guard_defaults(manifest: dict) -> dict:
    """
    Return resolved resource_guard section from a plugin manifest,
    filling defaults for missing keys.
    """
    rg = manifest.get("resource_guard") or {}
    return {
        "safe_temporary_stop": bool(rg.get("safe_temporary_stop", False)),
        "lifecycle_adapter": rg.get("lifecycle_adapter", ""),
        "operations": rg.get("operations", {}),
    }
