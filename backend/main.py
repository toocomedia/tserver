"""
main.py — FastAPI application entry point
Mounts routers, static files, and templates. Initializes DB on startup.
No routes defined here — all routes live in routers/.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from database import init_db
from routers import system, domains, dns, ssl, proxy, errors
from middleware.error_capture import RequestIdMiddleware, register_error_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB tables on startup."""
    logger.info("Initializing database...")
    await init_db()
    logger.info("Panel ready.")
    yield
    logger.info("Panel shutting down.")


app = FastAPI(
    title="VPS Control Panel",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# Request ID + error capture
app.add_middleware(RequestIdMiddleware)
register_error_handlers(app)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Routers
app.include_router(system.router)
app.include_router(domains.router)   # Phase 2
app.include_router(dns.router)       # Phase 3
app.include_router(ssl.router)       # Phase 4
app.include_router(proxy.router)     # Phase 5
app.include_router(errors.router)    # Phase 6
