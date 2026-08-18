"""
services/resources.py — Discoverable system resources (domains, apps, DBs, files) for permissions & context.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ai_helper import AiPermissionPolicy
from plugins.ai_helper import permissions

logger = logging.getLogger(__name__)


async def get_permission_policy(db: AsyncSession) -> AiPermissionPolicy:
    """Retrieves or creates the permission policy."""
    return await permissions.get_or_create_policy(db)


async def update_permission_policy(db: AsyncSession, data: Dict[str, Any]) -> AiPermissionPolicy:
    """Updates AI permission settings."""
    return await permissions.update_policy(db, data)


def get_audit_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieves recent tool call audit logs."""
    return permissions.audit.get_recent_audit_logs(limit)


async def get_discoverable_resources(db: AsyncSession) -> Dict[str, Any]:
    """
    Discovers all system resources (domains, apps, databases, file targets)
    for rendering interactive permission whitelists in the UI.
    """
    from models.domain import Domain
    from models.container_app import ContainerApp
    from models.hosted_app import HostedApp
    from models.php_website import PhpWebsite
    from models.container_app_database import ContainerAppDatabase
    from models.php_website_database import PhpWebsiteDatabase

    # 1. Domains
    domains_res = await db.execute(select(Domain).order_by(Domain.name.asc()))
    domains_all = domains_res.scalars().all()
    domains_map = {d.id: d.name for d in domains_all}
    domain_items = [
        {
            "id": d.id,
            "name": d.name,
            "project_type": d.project_type or "static",
            "nginx_active": bool(d.nginx_active),
        }
        for d in domains_all
    ]

    # 2. Applications (Container, Python, PHP)
    app_items = []
    
    c_res = await db.execute(select(ContainerApp).order_by(ContainerApp.id.desc()))
    for c in c_res.scalars().all():
        app_items.append({
            "id": str(c.id),
            "full_id": f"container:{c.id}",
            "name": c.container_name or f"container-app-{c.id}",
            "type": "container",
            "domain": domains_map.get(c.domain_id, ""),
            "status": c.status or "stopped",
        })

    py_res = await db.execute(select(HostedApp).order_by(HostedApp.id.desc()))
    for py in py_res.scalars().all():
        app_items.append({
            "id": str(py.id),
            "full_id": f"python:{py.id}",
            "name": f"python-app-{py.id}",
            "type": "python",
            "domain": domains_map.get(py.domain_id, ""),
            "status": py.status or "stopped",
        })

    php_res = await db.execute(select(PhpWebsite).order_by(PhpWebsite.id.desc()))
    for php in php_res.scalars().all():
        app_items.append({
            "id": str(php.id),
            "full_id": f"php:{php.id}",
            "name": f"php-site-{php.id}",
            "type": "php",
            "domain": domains_map.get(php.domain_id, ""),
            "status": php.status or "active",
        })

    # 3. Databases
    db_items = []
    c_dbs = (await db.execute(select(ContainerAppDatabase))).scalars().all()
    for cdb in c_dbs:
        if cdb.database_name:
            db_items.append({
                "name": cdb.database_name,
                "engine": cdb.provider or "postgresql",
                "app_id": cdb.app_id,
                "type": "container",
            })

    p_dbs = (await db.execute(select(PhpWebsiteDatabase))).scalars().all()
    for pdb in p_dbs:
        if pdb.database_name:
            db_items.append({
                "name": pdb.database_name,
                "engine": "mariadb",
                "site_id": pdb.site_id,
                "type": "php",
            })

    unique_dbs = []
    seen_db_names = set()
    for dbi in db_items:
        if dbi["name"].lower() not in seen_db_names:
            seen_db_names.add(dbi["name"].lower())
            unique_dbs.append(dbi)

    # 4. File Targets
    file_items = []
    try:
        from plugins.file_manager import file_targets
        targets = await file_targets.list_targets(db)
        for t in targets:
            file_items.append({
                "id": t.get("id"),
                "domain": t.get("domain") or "",
                "preset": t.get("preset") or t.get("id"),
                "type": t.get("target_type") or "app",
                "status": t.get("status") or "active",
            })
    except Exception as exc:
        logger.debug("Could not list file manager targets for permissions: %s", exc)

    return {
        "domains": domain_items,
        "apps": app_items,
        "databases": unique_dbs,
        "file_targets": file_items,
    }
