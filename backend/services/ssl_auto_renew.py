"""
services/ssl_auto_renew.py — Exact-time scheduler for SSL auto-renewal with retries.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import AsyncSessionLocal
from models.ssl_cert import SslCert
from models.notification import Notification
from services import ssl_service

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 3600
MAX_SLEEP_SECONDS = 3600 # Cap sleep at 1 hour to detect new certificates
RENEW_THRESHOLD_DAYS = 30

_next_retry: dict[int, datetime] = {}
_retry_count: dict[int, int] = {}


async def _add_notification(db: AsyncSession, type_: str, message: str):
    notif = Notification(type=type_, message=message)
    db.add(notif)
    await db.commit()


async def run_scheduler():
    """Background task to precisely schedule SSL renewals."""
    logger.info("SSL auto-renewal scheduler started.")
    await asyncio.sleep(10)  # Wait for startup to settle
    
    while True:
        try:
            async with AsyncSessionLocal() as db:
                certs = (await db.execute(
                    select(SslCert).where(SslCert.auto_renew == True)
                )).scalars().all()
                
                if not certs:
                    await asyncio.sleep(MAX_SLEEP_SECONDS)
                    continue
                
                now = datetime.now(timezone.utc)
                sleep_times = []
                processed_any = False
                
                for cert in certs:
                    if not cert.expiry_date:
                        continue
                        
                    expiry = cert.expiry_date
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                        
                    target_date = expiry - timedelta(days=RENEW_THRESHOLD_DAYS)
                    
                    if cert.id in _next_retry:
                        target_date = _next_retry[cert.id]
                        
                    if target_date <= now:
                        logger.info("Auto-renewing SSL certificate for %s", cert.full_domain)
                        try:
                            await ssl_service.renew_cert(db, cert.id)
                            await _add_notification(db, "success", f"Successfully auto-renewed SSL certificate for {cert.full_domain}.")
                            if cert.id in _next_retry:
                                del _next_retry[cert.id]
                            if cert.id in _retry_count:
                                del _retry_count[cert.id]
                        except Exception as e:
                            count = _retry_count.get(cert.id, 0) + 1
                            _retry_count[cert.id] = count
                            
                            if count >= MAX_RETRIES:
                                logger.error("Failed to auto-renew %s after %d attempts.", cert.full_domain, MAX_RETRIES)
                                await _add_notification(
                                    db, "error", 
                                    f"Failed to auto-renew SSL for {cert.full_domain} after {MAX_RETRIES} attempts. Last error: {str(e)}"
                                )
                                # Stop retrying today. Next attempt tomorrow.
                                _next_retry[cert.id] = now + timedelta(days=1)
                                _retry_count[cert.id] = 0
                            else:
                                logger.warning("Failed to auto-renew %s. Attempt %d/%d. Retrying in 1 hour.", cert.full_domain, count, MAX_RETRIES)
                                _next_retry[cert.id] = now + timedelta(seconds=RETRY_DELAY_SECONDS)
                                
                        processed_any = True
                        break # Only process one cert per DB session cycle to keep transactions short
                    else:
                        sleep_times.append((target_date - now).total_seconds())
                
                if processed_any:
                    # If we processed one, there might be more ready, sleep minimally
                    await asyncio.sleep(1)
                elif sleep_times:
                    # Sleep until the next certificate is ready (capped at 1 hour)
                    next_sleep = min(sleep_times)
                    next_sleep = max(1, min(next_sleep, MAX_SLEEP_SECONDS))
                    await asyncio.sleep(next_sleep)
                else:
                    await asyncio.sleep(MAX_SLEEP_SECONDS)

        except Exception as exc:
            logger.exception("SSL auto-renew scheduler loop error: %s", exc)
            await asyncio.sleep(60)
