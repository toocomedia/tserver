"""
plugins/external_dns/operations.py — External DNS record operations.

Thin async wrappers that resolve a domain's binding, build its provider adapter,
and delegate record CRUD. Read health (status/last_error) is refreshed on list so
the landing page reflects reality. Kept separate from service.py for modularity;
imports the service (no cycle — service never imports this module).
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from plugins.external_dns.providers.base import CredentialsError, ExternalDnsError
from plugins.external_dns.service import external_dns_service

logger = logging.getLogger(__name__)


async def _bound(db: AsyncSession, domain_name: str):
    binding = await external_dns_service.get_binding(db, domain_name)
    if binding is None:
        raise ExternalDnsError(
            "Domain is not connected to an external DNS provider.", status_code=400
        )
    return binding, external_dns_service.adapter_for(binding)


def _check_type(binding, rtype: str) -> None:
    allowed = external_dns_service.supported_types(binding.provider)
    if allowed and (rtype or "").strip().upper() not in allowed:
        raise ExternalDnsError(
            f"Record type {rtype} is not supported by this provider.", status_code=400
        )


async def list_records(db: AsyncSession, domain_name: str) -> list[dict]:
    binding, adapter = await _bound(db, domain_name)
    try:
        records = await adapter.list_records()
    except CredentialsError as exc:
        await external_dns_service.set_status(db, binding, "error", exc.message)
        raise
    await external_dns_service.set_status(db, binding, "active", None)
    return [rec.to_row() for rec in records]


async def add_record(db: AsyncSession, domain_name: str, name: str, rtype: str, content: str, ttl: int = 3600) -> dict:
    binding, adapter = await _bound(db, domain_name)
    _check_type(binding, rtype)
    rec = await adapter.add_record(name, rtype, content, ttl)
    return rec.to_row()


async def update_record(db: AsyncSession, domain_name: str, record_id: str, name: str, rtype: str, content: str, ttl: int = 3600) -> dict:
    binding, adapter = await _bound(db, domain_name)
    _check_type(binding, rtype)
    rec = await adapter.update_record(record_id, name, rtype, content, ttl)
    return rec.to_row()


async def delete_record(db: AsyncSession, domain_name: str, record_id: str, name: str, rtype: str, content: str) -> None:
    binding, adapter = await _bound(db, domain_name)
    if (rtype or "").strip().upper() == "SOA":
        raise ExternalDnsError("SOA records cannot be deleted.", status_code=400)
    await adapter.delete_record(record_id, name, rtype, content)


async def push_records(db: AsyncSession, domain_name: str, rows: list[dict]) -> dict:
    """Create panel-provided records in the provider (one-time import on switch).

    SOA/NS are skipped (the provider owns zone infrastructure); unsupported types
    are reported, not fatal. Returns a summary so the caller can surface it.
    """
    binding, adapter = await _bound(db, domain_name)
    allowed = external_dns_service.supported_types(binding.provider)
    added = 0
    errors: list[str] = []
    for row in rows:
        rtype = (row.get("type") or "").strip().upper()
        name = row.get("name") or "@"
        content = row.get("content") or ""
        if rtype in ("SOA", "NS") or not content:
            continue
        if allowed and rtype not in allowed:
            errors.append(f"{name} ({rtype}): unsupported by provider")
            continue
        try:
            await adapter.add_record(name, rtype, content, int(row.get("ttl") or 3600))
            added += 1
        except Exception as exc:
            errors.append(f"{name} ({rtype}): {getattr(exc, 'message', str(exc))}")
    return {"added": added, "failed": len(errors), "errors": errors[:10]}
