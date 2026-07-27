"""Shared hosted-app route lookups."""
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import Domain
from models.hosted_app import HostedApp


async def get_app(db: AsyncSession, app_id: int) -> HostedApp:
    app = await db.get(HostedApp, app_id)
    if app is None:
        raise HTTPException(404, "Python app not found.")
    return app


async def get_domain(db: AsyncSession, domain_id: int) -> Domain:
    domain = await db.get(Domain, domain_id)
    if domain is None:
        raise HTTPException(404, "Domain not found.")
    return domain
