"""Filament preset lifecycle kept separate from Laravel base setup."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

from models.php_website import PhpWebsite
from services import php_site_filament_runtime as runtime


PRESET = "filament"


async def options() -> dict[str, Any]:
    from pathlib import Path
    composer_binary = Path("/usr/local/bin/composer")
    composer_available = bool(composer_binary.is_file() and not composer_binary.is_symlink())
    try:
        res = await asyncio.to_thread(runtime.status)
        if res.get("composer_available") or composer_available:
            res["composer_available"] = True
        return res
    except RuntimeError as exc:
        return {"composer_available": composer_available, "error": str(exc)}


async def ensure_requirements() -> None:
    from pathlib import Path
    try:
        status = await asyncio.to_thread(runtime.status)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    composer_binary = Path("/usr/local/bin/composer")
    composer_available = status.get("composer_available", False) or bool(composer_binary.is_file() and not composer_binary.is_symlink())
    if not composer_available:
        raise HTTPException(409, "Panel-managed Composer is not installed. Please install Composer from Dependencies -> PHP Runtime -> Panel Tools first.")


async def install(site: PhpWebsite, domain: str, values: dict[str, str]) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(runtime.install, site, domain, values)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
