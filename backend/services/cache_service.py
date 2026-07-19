"""
services/cache_service.py — Manage Nginx disk caches for proxies.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import config
from models.proxy import ReverseProxy
from utils.validators import sanitize_domain

logger = logging.getLogger(__name__)


def _get_cache_dir(full_domain: str) -> Path | None:
    """Return the cache directory path for a given domain."""
    if not config.NGINX_CACHE_DIR:
        return None
    safe_zone = sanitize_domain(full_domain).replace(".", "_")
    return Path(config.NGINX_CACHE_DIR) / safe_zone


async def purge_proxy_cache(full_domain: str) -> bool:
    """
    Delete the Nginx cache directory for a domain.
    Returns True if purged, False if it didn't exist or failed.
    """
    cache_dir = _get_cache_dir(full_domain)
    if not cache_dir or not cache_dir.exists():
        return False
        
    try:
        shutil.rmtree(cache_dir)
        # Nginx will automatically recreate the folder hierarchy when needed
        return True
    except Exception as exc:
        logger.error("Failed to purge cache for %s: %s", full_domain, exc)
        return False


def get_cache_size_mb(full_domain: str) -> float:
    """Calculate the total size of the cache directory in MB."""
    cache_dir = _get_cache_dir(full_domain)
    if not cache_dir or not cache_dir.exists():
        return 0.0
        
    try:
        total_size = sum(f.stat().st_size for f in cache_dir.rglob('*') if f.is_file())
        return round(total_size / (1024 * 1024), 2)
    except Exception as exc:
        logger.warning("Failed to calculate cache size for %s: %s", full_domain, exc)
        return 0.0


async def run_auto_purge_all(db: AsyncSession) -> int:
    """
    Check all proxies and purge caches for those that have exceeded their auto-clear TTL.
    Returns the number of caches purged.
    """
    stmt = select(ReverseProxy).where(
        ReverseProxy.cache_enabled == True,
        ReverseProxy.cache_auto_clear_hours > 0
    )
    result = await db.execute(stmt)
    proxies = result.scalars().all()
    
    now = datetime.now(timezone.utc)
    purged_count = 0
    
    for proxy in proxies:
        # If never cleared, use created_at (assumed) or now
        last_cleared = proxy.last_cache_cleared
        if not last_cleared:
            # First time running auto-purge for this proxy
            proxy.last_cache_cleared = now
            continue
            
        # Ensure timezone-aware comparison
        if last_cleared.tzinfo is None:
            last_cleared = last_cleared.replace(tzinfo=timezone.utc)
            
        expiry_time = last_cleared + timedelta(hours=proxy.cache_auto_clear_hours)
        if now >= expiry_time:
            # Time to purge
            if await purge_proxy_cache(proxy.full_domain):
                proxy.last_cache_cleared = now
                purged_count += 1
                
    if purged_count > 0:
        await db.commit()
        
    return purged_count
