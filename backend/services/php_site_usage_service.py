"""Live per-website process usage from tracked native PHP websites."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import Domain
from models.php_website import PhpWebsite


async def get_usage(db: AsyncSession, processes: list[dict], total_memory: int) -> list[dict]:
    """Calculate CPU and RAM metrics for each native PHP website."""
    rows = (await db.execute(select(PhpWebsite, Domain.name).join(Domain, PhpWebsite.domain_id == Domain.id))).all()
    usage = []
    for site, domain_name in rows:
        user_name = f"srvphp{site.id}".lower()
        linux_user = str(site.linux_user or "").strip().lower()
        pool_marker = f"srv-panel-site-{site.id}".lower()
        sock_marker = f"srv-site-{site.id}".lower()
        root_path = str(site.root_path or "").strip().lower()

        matches = []
        for p in processes:
            p_user = str(p.get("username") or "").lower()
            p_cmd = " ".join(p.get("cmdline") or []).lower()

            # Dedicated Linux user match (e.g. srvphp1)
            if p_user and (p_user == user_name or p_user == linux_user):
                matches.append(p)
                continue

            # Pool, socket or dedicated username in command line
            if pool_marker in p_cmd or sock_marker in p_cmd or user_name in p_cmd:
                matches.append(p)
                continue

            # Document root path in command line (if specific enough)
            if root_path and len(root_path) > 8 and root_path in p_cmd:
                matches.append(p)

        memory = sum(int(getattr(p.get("memory_info"), "rss", 0) or 0) for p in matches)
        mem_pct = round(memory / total_memory * 100, 1) if total_memory else 0.0

        if matches:
            status = "running"
        elif site.status in ("active", "healthy"):
            status = "active"
        else:
            status = site.status

        preset_title = "WordPress" if site.preset == "wordpress" else f"PHP {site.php_version}"
        usage.append({
            "label": domain_name,
            "domain": domain_name,
            "site_id": site.id,
            "version": site.php_version,
            "preset": site.preset,
            "service": preset_title,
            "count": len(matches),
            "cpu": round(sum(float(p.get("cpu_percent") or 0) for p in matches), 1),
            "mem": mem_pct,
            "memory": f"{memory / (1024 ** 2):.0f} MB ({mem_pct:.1f}% of server)" if total_memory else "0 MB",
            "status": status,
        })
    return usage
