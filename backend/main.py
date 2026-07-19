"""
main.py — FastAPI application entry point
Mounts routers, static files, and templates. Initializes DB on startup.
No routes defined here — all routes live in routers/.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import config
from database import init_db
from routers import system, domains, dns, ssl, proxy, errors, auth, settings, dev
from middleware.error_capture import RequestIdMiddleware, register_error_handlers
from middleware.auth import AuthMiddleware
from middleware.security_headers import SecurityHeadersMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _auto_purge_loop() -> None:
    """Background task: check and clear expired proxy caches every hour."""
    from database import AsyncSessionLocal
    from services import cache_service

    while True:
        await asyncio.sleep(3600)  # run every hour
        try:
            async with AsyncSessionLocal() as db:
                count = await cache_service.run_auto_purge_all(db)
                if count:
                    logger.info("Auto-purge: %d proxy cache(s) cleared", count)
        except Exception as exc:
            logger.warning("Auto-purge loop error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB tables on startup and start background tasks."""
    if getattr(config, "_SECRET_KEY_EPHEMERAL", False):
        logger.warning(
            "SECRET_KEY was missing — using ephemeral key. "
            "Add SECRET_KEY to .env (or re-run update/create_admin) "
            "so login sessions survive restarts."
        )
    logger.info("Initializing database...")
    await init_db()
    purge_task = asyncio.create_task(_auto_purge_loop())
    logger.info("Panel ready.")
    yield
    purge_task.cancel()
    logger.info("Panel shutting down.")


app = FastAPI(
    title="VPS Control Panel",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# Middleware order: last added runs first on the request.
# ProxyHeaders → Session → SecurityHeaders → RequestId → Auth → app
# ProxyHeaders: honor X-Forwarded-* so redirects keep :8080 when behind nginx
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    session_cookie="srv_panel_session",
    max_age=config.SESSION_MAX_AGE,
    same_site="lax",
    # Never force Secure cookies here if SESSION_HTTPS_ONLY was flipped by mistake —
    # that locks HTTP IP login. Prefer explicit HTTPS only after panel SSL works.
    https_only=bool(config.SESSION_HTTPS_ONLY),
)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
register_error_handlers(app)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Routers
app.include_router(auth.router)
app.include_router(system.router)
app.include_router(settings.router)
app.include_router(domains.router)   # Phase 2
app.include_router(dns.router)       # Phase 3
app.include_router(ssl.router)       # Phase 4
app.include_router(proxy.router)     # Phase 5
app.include_router(errors.router)    # Phase 6
app.include_router(dev.router)       # Testing tools
