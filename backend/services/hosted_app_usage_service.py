"""Live per-application process usage from tracked hosted Python apps."""
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from models.domain import Domain
from models.hosted_app import HostedApp


async def get_usage(db: AsyncSession, processes: list[dict], total_memory: int) -> list[dict]:
    rows = (await db.execute(select(HostedApp, Domain.name).join(Domain))).all()
    usage = []
    for app, domain_name in rows:
        root = str(Path(config.APP_HOSTING_ROOT) / str(app.id)).lower()
        matches = [p for p in processes if root in " ".join(p.get("cmdline") or []).lower()]
        memory = sum(int(getattr(p.get("memory_info"), "rss", 0) or 0) for p in matches)
        usage.append({
            "id": app.id, "label": domain_name, "service": app.service_name, "count": len(matches),
            "cpu": round(sum(float(p.get("cpu_percent") or 0) for p in matches), 1),
            "mem": round(memory / total_memory * 100, 1) if total_memory else 0,
            "memory": f"{memory / (1024 ** 2):.0f} MB ({memory / total_memory * 100:.1f}% of server)" if total_memory else "0 MB",
            "status": "running" if matches else app.status,
        })
    return usage
