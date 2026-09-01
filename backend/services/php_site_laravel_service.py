"""Laravel preset policy kept separate from generic PHP website lifecycle code."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

from models.php_website import PhpWebsite
from services import php_site_laravel_runtime as runtime


PRESET = "laravel"
FILAMENT_PRESET = "filament"
PRESETS = frozenset({PRESET, FILAMENT_PRESET})


def is_laravel_preset(preset: str) -> bool:
    return preset in PRESETS


def requires_database(preset: str) -> bool:
    return preset == "wordpress" or is_laravel_preset(preset)


def install_profile(preset: str) -> str:
    return "plugin_install" if preset == "wordpress" or is_laravel_preset(preset) else "native_light"


async def options(versions: list[dict[str, Any]]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    composer_available = False
    for item in versions:
        version = str(item["version"])
        try:
            result = await asyncio.to_thread(runtime.status, version)
        except RuntimeError as exc:
            result = {"ready": False, "composer_available": False, "missing_packages": [], "error": str(exc)}
        results[version] = result
        composer_available = composer_available or bool(result.get("composer_available"))
    return {"composer_available": composer_available, "versions": results}


async def ensure_requirements(version: str, *, install: bool) -> dict[str, Any]:
    try:
        status = await asyncio.to_thread(runtime.status, version)
        if not status.get("composer_available"):
            raise HTTPException(409, "Panel-managed Composer is not installed. Please install Composer from Dependencies -> PHP Runtime -> Panel Tools first.")
        if status.get("ready"):
            return status
        missing = ", ".join(status.get("missing_packages") or []) or "required Laravel PHP extensions"
        if not install:
            raise HTTPException(409, f"Missing {missing}. Retry with install_missing_extensions enabled.")
        await asyncio.to_thread(runtime.install_extensions, version)
        status = await asyncio.to_thread(runtime.status, version)
        if not status.get("ready"):
            raise HTTPException(502, "Laravel PHP extensions remain unavailable after installation.")
        return status
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


async def install(
    site: PhpWebsite, domain: str, database: dict[str, str], *, https: bool,
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(runtime.install, site, domain, database, https=https)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


async def update_url(site: PhpWebsite, domain: str, *, https: bool) -> None:
    try:
        await asyncio.to_thread(runtime.update_url, site, domain, https=https)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


async def update_database_password(site: PhpWebsite, domain: str, database: dict[str, str]) -> None:
    try:
        await asyncio.to_thread(runtime.update_database_password, site, domain, database)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
