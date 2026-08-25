"""Official Docker registry image advisor for Git-based applications."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Known popular self-hosted software best deployed via official Docker images
_OFFICIAL_IMAGE_CATALOG: list[dict[str, Any]] = [
    {
        "pattern": re.compile(r"(?:^|/|_)jellyfin(?:/|$|_|\.git)", re.IGNORECASE),
        "name": "Jellyfin",
        "recommended_image": "jellyfin/jellyfin:latest",
        "recommended_port": 8096,
        "health_path": "disabled",
        "reason": "Jellyfin is officially distributed as a pre-compiled Docker container. Building from .NET C# source code is resource-heavy and requires specialized SDKs.",
    },
    {
        "pattern": re.compile(r"(?:^|/|_)vaultwarden(?:/|$|_|\.git)", re.IGNORECASE),
        "name": "Vaultwarden",
        "recommended_image": "vaultwarden/server:latest",
        "recommended_port": 80,
        "health_path": "/alive",
        "reason": "Vaultwarden is officially distributed as a pre-compiled container with optimized SQLite/PostgreSQL drivers.",
    },
    {
        "pattern": re.compile(r"(?:^|/|_)umami(?:/|$|_|\.git)", re.IGNORECASE),
        "name": "Umami",
        "recommended_image": "docker.umami.is/umami-software/umami:latest",
        "recommended_port": 3000,
        "health_path": "/api/heartbeat",
        "reason": "Umami maintains an official multi-arch Docker image with pre-bundled analytics migration scripts.",
    },
    {
        "pattern": re.compile(r"(?:^|/|_)ghost(?:/|$|_|\.git)", re.IGNORECASE),
        "name": "Ghost",
        "recommended_image": "ghost:5-alpine",
        "recommended_port": 2368,
        "health_path": "/",
        "reason": "Ghost provides an official container image pre-configured with Node.js runtime and content directories.",
    },
    {
        "pattern": re.compile(r"(?:^|/|_)n8n(?:/|$|_|\.git)", re.IGNORECASE),
        "name": "n8n",
        "recommended_image": "docker.n8n.io/n8nio/n8n:latest",
        "recommended_port": 5678,
        "health_path": "/healthz",
        "reason": "n8n is officially distributed as a pre-packaged workflow automation container.",
    },
    {
        "pattern": re.compile(r"(?:^|/|_)pocketbase(?:/|$|_|\.git)", re.IGNORECASE),
        "name": "PocketBase",
        "recommended_image": "ghcr.io/muchobien/pocketbase:latest",
        "recommended_port": 8090,
        "health_path": "/api/health",
        "reason": "PocketBase is a single Go binary with official container distributions for instant startup.",
    },
]


def advise_official_image(repository_url: str, framework: str = "") -> Optional[Dict[str, Any]]:
    """Checks if a Git repository corresponds to an established app with an official Docker image."""
    repo = (repository_url or "").strip()
    target_text = f"{repo} {framework}".lower()
    if not repo:
        return None

    for entry in _OFFICIAL_IMAGE_CATALOG:
        if entry["pattern"].search(target_text):
            return {
                "has_official_image": True,
                "app_name": entry["name"],
                "recommended_image": entry["recommended_image"],
                "recommended_port": entry["recommended_port"],
                "recommended_health_path": entry["health_path"],
                "reason": entry["reason"],
            }
    return None
