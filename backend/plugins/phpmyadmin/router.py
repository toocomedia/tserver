"""phpMyAdmin: panel-served proxy + minimal landing page."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from plugins.phpmyadmin.service import phpmyadmin_service
from templating import templates

router = APIRouter(prefix="/phpmyadmin", tags=["phpmyadmin"])
logger = logging.getLogger(__name__)

# Headers forwarded verbatim to phpMyAdmin.
_PASS_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-language",
        "content-type",
        "cookie",
        "origin",
        "referer",
        "user-agent",
        "x-requested-with",
    }
)
_PASS_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "content-disposition",
        "content-language",
        "cache-control",
        "expires",
        "last-modified",
        "etag",
        "location",
        "set-cookie",
        "www-authenticate",
        "x-powered-by",
    }
)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from plugins.manager import plugin_manager

    plugin = plugin_manager.plugins.get("phpmyadmin")
    return templates.TemplateResponse(
        "phpmyadmin.html",
        {
            "request": request,
            "active_page": "plugins",
            "plugin_version": (plugin or {}).get("version", "1.0.0"),
            "status": phpmyadmin_service.get_status(),
        },
    )


@router.post("/api/uninstall")
async def uninstall_phpmyadmin(request: Request):
    from plugins.manager import plugin_manager

    success, message = await plugin_manager.run_plugin_script(
        "phpmyadmin", "uninstall"
    )
    return JSONResponse(
        {"status": "ok" if success else "error", "message": message},
        status_code=200 if success else 500,
    )


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def proxy_phpmyadmin(request: Request, path: str):
    """Stream requests to the local phpMyAdmin server, keeping panel auth."""
    if not phpmyadmin_service.is_installed():
        return RedirectResponse("/phpmyadmin/", status_code=303)

    upstream_path = f"/{path}" if path else "/"
    url = f"{phpmyadmin_service.base_url}{upstream_path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() in _PASS_REQUEST_HEADERS
    }
    body = await request.body()

    client = httpx.AsyncClient(timeout=None)
    try:
        upstream = await client.request(
            request.method,
            url,
            headers=headers,
            content=body or None,
        )
        content = upstream.content
    except httpx.HTTPError as exc:
        logger.warning("phpMyAdmin proxy failed: %s", exc)
        return JSONResponse(
            {"detail": "phpMyAdmin is not running. Install it from the plugin page."},
            status_code=502,
        )
    finally:
        await client.aclose()

    response_headers = [
        (name, value)
        for name, value in upstream.headers.items()
        if name.lower() in _PASS_RESPONSE_HEADERS
    ]
    # phpMyAdmin redirects to absolute paths; keep them inside /phpmyadmin.
    for index, (name, value) in enumerate(response_headers):
        if name.lower() == "location" and value.startswith("/"):
            value = (
                f"/phpmyadmin{value}"
                if not value.startswith("/phpmyadmin")
                else value
            )
            response_headers[index] = (name, value)

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=dict(response_headers),
    )
