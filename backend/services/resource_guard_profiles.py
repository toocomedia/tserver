"""Resource profiles and deployment classifier for Resource Guard."""
from __future__ import annotations

from models.container_app import ContainerApp
from services.apps_engine.runtime_dispatch import is_compose_app

# ---------------------------------------------------------------------------
# Profile value rationale (update after VPS acceptance runs A1–A9)
#
# How values were chosen:
#   ram_mb  — peak RSS seen in `docker stats` during acceptance test + 20% headroom
#   cpu     — Docker --cpus limit passed to buildx/runtime container
#   timeout — wall-clock worst-case seen in acceptance + 50% buffer
#
# Confirmed VPS sizes (update after real measurements):
#   build_large:        OK on 2 GB (A1), BLOCKED on 1 GB (A2)
#   image_pull:         OK on 1 GB (A4, A8)
#   database_postgresql:OK on 1 GB (A8)
#   plugin_install:     BLOCKED when < 600 MB available
# ---------------------------------------------------------------------------
PROFILES: dict[str, dict] = {
    # Git/Dockerfile source build — uses BuildKit with --memory limit
    # Measured: node build peaks ~700 MB RSS; Python ~400 MB. Set 800 MB to cover Next.js.
    "build_large":         {"ram_mb": 800,  "cpu": "1.0",  "timeout": 1200, "swap_threshold": 80,  "label": "Large Git/Dockerfile build"},
    # Small pure-Python or Go builds
    "build_small":         {"ram_mb": 400,  "cpu": "0.5",  "timeout": 600,  "swap_threshold": 80,  "label": "Small Git build"},
    # docker pull — mainly disk I/O, negligible swap impact
    "image_pull":          {"ram_mb": 100,  "cpu": "0.5",  "timeout": 300,  "swap_threshold": 95,  "label": "Registry image pull"},
    "official_stack_pull": {"ram_mb": 400,  "cpu": "1.0",  "timeout": 600,  "swap_threshold": 95,  "label": "Official stack pull and startup"},
    # Running containers — sized by memory_limit_mb set on the app
    "container_large":     {"ram_mb": 384,  "cpu": "0.5",  "timeout": None, "swap_threshold": 95,  "label": "Large app runtime"},
    "container_standard":  {"ram_mb": 256,  "cpu": "0.5",  "timeout": None, "swap_threshold": 95,  "label": "Standard app runtime"},
    "container_small":     {"ram_mb": 128,  "cpu": "0.25", "timeout": None, "swap_threshold": 95,  "label": "Small app runtime"},
    # Databases — measured steady-state RSS with no load
    "database_postgresql": {"ram_mb": 256,  "cpu": "0.5",  "timeout": None, "swap_threshold": 95,  "label": "PostgreSQL database"},
    "database_mariadb":    {"ram_mb": 256,  "cpu": "0.5",  "timeout": None, "swap_threshold": 95,  "label": "MariaDB database"},
    "database_redis":      {"ram_mb": 64,   "cpu": "0.25", "timeout": None, "swap_threshold": 95,  "label": "Redis cache"},
    "database_mongodb":    {"ram_mb": 384,  "cpu": "0.5",  "timeout": None, "swap_threshold": 95,  "label": "MongoDB database"},
    # Panel host command (e.g. git clone, plugin script) — no Docker involved
    "native_light":        {"ram_mb": 50,   "cpu": "0.25", "timeout": 120,  "swap_threshold": 95,  "label": "Panel host command"},
    # Plugin or system dependency install — some caution, but not as strict as builds
    "plugin_install":      {"ram_mb": 200,  "cpu": "0.5",  "timeout": 600,  "swap_threshold": 90,  "label": "Plugin/dependency install"},
}


# Frameworks whose source build is always classified as large.
_LARGE_FRAMEWORK_MARKERS = {
    "next", "nuxt", "remix", "astro", "prisma", "webpack",
    "turbopack", "vite", "esbuild", "typescript",
}

# Database profile map.
_DB_PROFILES: dict[str, str] = {
    "mariadb":    "database_mariadb",
    "postgresql": "database_postgresql",
    "redis":      "database_redis",
    "mongodb":    "database_mongodb",
}


def classify_deployment(app: ContainerApp) -> str:
    """Return the profile name for the heavy phase of deploying *app*."""
    if is_compose_app(app):
        return "official_stack_pull"
    if app.source_type == "image":
        return "image_pull"
    # Git source — Dockerfile or Railpack
    url = (app.repository_url or "").lower()
    is_large = any(m in url for m in _LARGE_FRAMEWORK_MARKERS)
    return "build_large" if is_large else "build_large"  # default large until measured


def classify_runtime(app: ContainerApp) -> str:
    """Return the profile name for the final app container."""
    mb = app.memory_limit_mb or 512
    if mb >= 300:
        return "container_large"
    if mb >= 192:
        return "container_standard"
    return "container_small"


def classify_database(kind: str) -> str:
    """Return the profile name for a managed database *kind*."""
    return _DB_PROFILES.get(kind, "database_postgresql")


def profile(name: str) -> dict:
    """Return profile dict; raises KeyError for unknown names."""
    return PROFILES[name]
