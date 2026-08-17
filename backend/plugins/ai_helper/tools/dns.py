"""
tools/dns.py — Tool handler for PowerDNS zone records.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.dns_record import DnsRecord
from models.domain import Domain


async def get_dns_records(
    db: AsyncSession,
    domain: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Queries DNS records for a given domain zone."""
    cleaned_domain = domain.strip().rstrip(".")
    if not cleaned_domain:
        return {"status": "error", "message": "Domain is required."}

    # Find the domain in database
    d_stmt = select(Domain).where(Domain.name == cleaned_domain)
    d_res = await db.execute(d_stmt)
    dom = d_res.scalar_one_or_none()

    stmt = select(DnsRecord)
    if dom:
        stmt = stmt.where(DnsRecord.domain_id == dom.id)
    else:
        stmt = stmt.where(DnsRecord.name.ilike(f"%{cleaned_domain}%"))

    result = await db.execute(stmt)
    records = result.scalars().all()

    output = [
        {
            "id": r.id,
            "name": r.name,
            "type": r.type,
            "content": r.content,
            "ttl": r.ttl,
            "managed": r.managed,
        }
        for r in records
    ]

    return {
        "status": "ok",
        "domain": cleaned_domain,
        "count": len(output),
        "records": output,
    }
