"""Resource profiles and deployment classifier for Resource Guard."""
from __future__ import annotations

from models.container_app import ContainerApp

# Each profile defines the resource budget for one category of operation.
# ram_mb: reservation used in admission test (safe capacity check).
# cpu:    ceiling passed to Docker/Buildx.
# timeout: hard process timeout in seconds (None = runtime, no hard stop).
PROFILES: dict[str, dict] = {
    "build_large":         {"ram_mb": 800,  "cpu": "1.0",  "timeout": 1200, "label": "Large Git/Dockerfile build"},
    "build_small":         {"ram_mb": 400,  "cpu": "0.5",  "timeout": 600,  "label": "Small Git build"},
    "image_pull":          {"ram_mb": 100,  "cpu": "0.5",  "timeout": 300,  "label": "Registry image pull"},
    "container_large":     {"ram_mb": 384,  "cpu": "0.5",  "timeout": None, "label": "Large app runtime"},
    "container_standard":  {"ram_mb": 256,  "cpu": "0.5",  "timeout": None, "label": "Standard app runtime"},
    "container_small":     {"ram_mb": 128,  "cpu": "0.25", "timeout": None, "label": "Small app runtime"},
    "database_postgresql": {"ram_mb": 256,  "cpu": "0.5",  "timeout": None, "label": "PostgreSQL database"},
    "database_mariadb":    {"ram_mb": 256,  "cpu": "0.5",  "timeout": None, "label": "MariaDB database"},
    "database_redis":      {"ram_mb": 64,   "cpu": "0.25", "timeout": None, "label": "Redis cache"},
    "database_mongodb":    {"ram_mb": 384,  "cpu": "0.5",  "timeout": None, "label": "MongoDB database"},
    "native_light":        {"ram_mb": 50,   "cpu": "0.25", "timeout": 120,  "label": "Panel host command"},
    "plugin_install":      {"ram_mb": 200,  "cpu": "0.5",  "timeout": 600,  "label": "Plugin/dependency install"},
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
