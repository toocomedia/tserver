"""
services/external_dns_bridge.py — Core ↔ External DNS plugin bridge.

The core DNS Manager (routers/dns.py) delegates to a domain's external provider
through this module WITHOUT importing the plugin at load time. If the plugin is
missing or disabled, every hook signals "not external" and the caller falls back
to PowerDNS — so core behavior is byte-for-byte unchanged when the plugin is off.

Provider errors are converted to HTTPException here so core never needs to know
about the plugin's exception types.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PLUGIN_ID = "external_dns"


def _load() -> tuple[Any, Any]:
    """Return (service, operations) only when the plugin is active; else (None, None)."""
    try:
        from plugins.manager import plugin_manager
        info = plugin_manager.get_plugin(PLUGIN_ID)
        if not info or info.get("effective_status") != "active":
            return None, None
        from plugins.external_dns import operations
        from plugins.external_dns.service import external_dns_service
        return external_dns_service, operations
    except Exception as exc:  # plugin removed/broken → behave as if absent
        logger.debug("External DNS bridge unavailable: %s", exc)
        return None, None


def _http(exc: Exception) -> HTTPException:
    status = getattr(exc, "status_code", 502)
    detail = getattr(exc, "message", None) or str(exc)
    return HTTPException(status_code=status, detail=detail)


def plugin_active() -> bool:
    service, _ = _load()
    return service is not None


async def is_bound(db: AsyncSession, domain_name: str) -> bool:
    """True when the plugin is active AND this domain has an external binding."""
    service, _ = _load()
    if service is None:
        return False
    return await service.get_binding(db, domain_name) is not None


def all_providers() -> list[dict]:
    """Provider metadata (ids/keys only) for the connect modal."""
    service, _ = _load()
    return service.providers() if service is not None else []


async def bindings_map(db: AsyncSession) -> dict[str, str]:
    """{domain_name: provider_id} for every bound domain (DNS zones list badge)."""
    service, _ = _load()
    if service is None:
        return {}
    try:
        return {b["domain"]: b["provider"] for b in await service.list_bindings(db)}
    except Exception as exc:
        logger.warning("External DNS bindings_map failed: %s", exc)
        return {}


async def view_context(db: AsyncSession, domain_name: str) -> dict:
    """Everything the DNS records page needs about external DNS, in one call."""
    ctx: dict[str, Any] = {
        "plugin_active": False, "bound": False, "provider": None, "label_key": None,
        "capabilities": {}, "supported_types": [], "binding": None, "rows": [],
        "providers": [], "error": None,
    }
    service, operations = _load()
    if service is None:
        return ctx
    ctx["plugin_active"] = True
    ctx["providers"] = service.providers()

    binding = await service.get_binding(db, domain_name)
    if binding is None:
        return ctx

    meta = next((m for m in ctx["providers"] if m["id"] == binding.provider), None)
    ctx.update({
        "bound": True,
        "provider": binding.provider,
        "label_key": meta["label_key"] if meta else binding.provider,
        "capabilities": meta["capabilities"] if meta else {},
        "supported_types": meta["supported_types"] if meta else [],
        "binding": service.binding_public(binding),
    })
    try:
        ctx["rows"] = await operations.list_records(db, domain_name)
    except Exception as exc:
        ctx["error"] = getattr(exc, "message", None) or str(exc)
        ctx["rows"] = []
    return ctx


async def add_record(db: AsyncSession, domain: str, name: str, rtype: str, content: str, ttl: int) -> dict:
    _, operations = _load()
    if operations is None:
        raise HTTPException(status_code=400, detail="External DNS plugin is not active.")
    try:
        return await operations.add_record(db, domain, name, rtype, content, ttl)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http(exc) from exc


async def update_record(db: AsyncSession, domain: str, record_id: str, name: str, rtype: str, content: str, ttl: int) -> dict:
    _, operations = _load()
    if operations is None:
        raise HTTPException(status_code=400, detail="External DNS plugin is not active.")
    try:
        return await operations.update_record(db, domain, record_id, name, rtype, content, ttl)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http(exc) from exc


async def delete_record(db: AsyncSession, domain: str, record_id: str, name: str, rtype: str, content: str) -> None:
    _, operations = _load()
    if operations is None:
        raise HTTPException(status_code=400, detail="External DNS plugin is not active.")
    try:
        await operations.delete_record(db, domain, record_id, name, rtype, content)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http(exc) from exc


async def push_records(db: AsyncSession, domain: str, rows: list[dict]) -> dict:
    """Import panel (PowerDNS) records into the domain's external provider."""
    _, operations = _load()
    if operations is None:
        raise HTTPException(status_code=400, detail="External DNS plugin is not active.")
    try:
        return await operations.push_records(db, domain, rows)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http(exc) from exc
