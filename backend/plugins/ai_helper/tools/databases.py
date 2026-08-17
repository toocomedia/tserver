"""
tools/databases.py — Tool handler for database instance metadata (zero credentials).
"""
from __future__ import annotations

from typing import Any, Dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app_database import ContainerAppDatabase
from models.php_website_database import PhpWebsiteDatabase


async def get_databases_overview(
    db: AsyncSession,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Lists database instances and attachments without sensitive credentials."""
    # 1. Container App Databases
    c_stmt = select(ContainerAppDatabase)
    c_res = await db.execute(c_stmt)
    c_dbs = c_res.scalars().all()

    container_db_list = [
        {
            "id": db_item.id,
            "app_id": db_item.app_id,
            "engine": db_item.provider,
            "kind": db_item.kind,
            "database_name": db_item.database_name,
            "username": db_item.username,
            "status": db_item.status,
            "network_alias": db_item.network_alias,
        }
        for db_item in c_dbs
    ]

    # 2. PHP Website Databases (MariaDB)
    p_stmt = select(PhpWebsiteDatabase)
    p_res = await db.execute(p_stmt)
    p_dbs = p_res.scalars().all()

    php_db_list = [
        {
            "id": pdb.id,
            "site_id": pdb.site_id,
            "engine": "mariadb",
            "database_name": pdb.database_name,
            "username": pdb.username,
            "status": pdb.status,
        }
        for pdb in p_dbs
    ]

    return {
        "status": "ok",
        "container_databases": container_db_list,
        "php_databases": php_db_list,
        "total_count": len(container_db_list) + len(php_db_list),
    }
