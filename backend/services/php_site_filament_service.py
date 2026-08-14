"""Filament preset lifecycle kept separate from Laravel base setup."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

from models.php_website import PhpWebsite
from services import php_site_filament_runtime as runtime


PRESET = "filament"


async def options() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(runtime.status)
    except RuntimeError as exc:
        return {"composer_available": False, "error": str(exc)}


async def ensure_requirements() -> None:
    try:
        status = await asyncio.to_thread(runtime.status)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not status.get("composer_available"):
        raise HTTPException(409, "Panel-managed Composer is unavailable. Run the SRV Panel updater first.")


async def install(site: PhpWebsite, domain: str, values: dict[str, str]) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(runtime.install, site, domain, values)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
