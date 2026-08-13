"""HTML Page routes for PHP Websites frontend."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import dependency_manager
from services import php_site_service as service
from templating import templates

router = APIRouter(prefix="/php-sites", tags=["php-sites-ui"])


def _render_unavailable(request: Request, status_code: int = 503):
    return templates.TemplateResponse(
        "pages/php_sites/unavailable.html",
        {
            "request": request,
            "active_page": "php_sites",
        },
        status_code=status_code,
    )


@router.get("/", response_class=HTMLResponse)
async def php_sites_index(request: Request, db: AsyncSession = Depends(get_db)):
    if not dependency_manager.is_healthy("php"):
        return _render_unavailable(request)
    sites = await service.list_sites(db)
    total_sites = len(sites)
    wp_sites = sum(1 for s in sites if s.get("preset") == "wordpress")
    active_sites = sum(1 for s in sites if s.get("status") == "active")
    return templates.TemplateResponse(
        "pages/php_sites/index.html",
        {
            "request": request,
            "active_page": "php_sites",
            "sites": sites,
            "total_sites": total_sites,
            "wp_sites": wp_sites,
            "active_sites": active_sites,
        },
    )


@router.get("/create", response_class=HTMLResponse)
async def php_sites_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    if not dependency_manager.is_healthy("php"):
        return _render_unavailable(request)
    opts = await service.options(db)
    return templates.TemplateResponse(
        "pages/php_sites/create.html",
        {
            "request": request,
            "active_page": "php_sites",
            "options": opts,
        },
    )


@router.get("/{site_id}", response_class=HTMLResponse)
async def php_sites_detail_page(site_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if not dependency_manager.is_healthy("php"):
        return _render_unavailable(request)
    try:
        site = await service.get_site(db, site_id)
        site_data = await service.serialize_site(db, site)
        opts = await service.options(db)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(404, "PHP website not found.") from exc
    return templates.TemplateResponse(
        "pages/php_sites/detail.html",
        {
            "request": request,
            "active_page": "php_sites",
            "site": site_data,
            "options": opts,
        },
    )
