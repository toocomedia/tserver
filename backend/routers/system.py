"""
routers/system.py — Health check + server status routes
Returns real nginx and PowerDNS status, not mocked data.
"""
import asyncio
import socket
import time
import httpx
try:
    import psutil as _psutil
    _PSUTIL_OK = True
except ImportError:
    _psutil = None  # type: ignore
    _PSUTIL_OK = False

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from fastapi import Depends

from database import get_db
from models.domain import Domain
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from services import error_service
from templating import templates
from utils.shell import run
import config

router = APIRouter()


async def _check_nginx() -> dict:
    result = await run(["nginx", "-t"])
    return {
        "ok": result.success,
        "detail": result.stderr if not result.success else "OK",
    }


async def _check_powerdns() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(
                f"{config.PDNS_URL}/api/v1/servers/localhost",
                headers={"X-API-Key": config.PDNS_API_KEY},
            )
        return {"ok": r.status_code == 200, "detail": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


@router.get("/api/health")
async def health_check():
    """Returns live status of nginx and PowerDNS."""
    nginx_status, pdns_status = await asyncio.gather(
        _check_nginx(), _check_powerdns()
    )
    return {
        "nginx": nginx_status,
        "powerdns": pdns_status,
        "server_ip": config.SERVER_IP,
        "hostname": socket.gethostname(),
    }


def _uptime_human(seconds: float) -> str:
    """Convert uptime seconds to a human-readable string."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


@router.get("/api/stats")
async def server_stats():
    """Live server resource stats via psutil — CPU, RAM, disk, network, processes."""
    from fastapi import HTTPException
    if not _PSUTIL_OK:
        raise HTTPException(
            status_code=503,
            detail="psutil not installed. Run: pip install psutil==6.0.0",
        )

    # CPU (non-blocking: interval=None returns cached value since last call)
    cpu_percent = _psutil.cpu_percent(interval=None)
    cpu_count = _psutil.cpu_count(logical=True)
    cpu_freq = _psutil.cpu_freq()
    freq_mhz = round(cpu_freq.current) if cpu_freq else None

    # RAM
    ram = _psutil.virtual_memory()

    # Swap
    swap = _psutil.swap_memory()

    # Disk — all mounted partitions
    disks = []
    for part in _psutil.disk_partitions(all=False):
        try:
            usage = _psutil.disk_usage(part.mountpoint)
            disks.append({
                "mount": part.mountpoint,
                "device": part.device,
                "total_gb": round(usage.total / (1024 ** 3), 1),
                "used_gb": round(usage.used / (1024 ** 3), 1),
                "free_gb": round(usage.free / (1024 ** 3), 1),
                "percent": usage.percent,
            })
        except PermissionError:
            pass

    # Network I/O
    net = _psutil.net_io_counters()

    # Uptime
    boot_ts = _psutil.boot_time()
    uptime_sec = time.time() - boot_ts

    # Top 15 processes by CPU usage
    procs = []
    for p in _psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
        try:
            info = p.info
            if info["cpu_percent"] is not None:
                procs.append(info)
        except (_psutil.NoSuchProcess, _psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x["cpu_percent"] or 0, reverse=True)
    top_procs = [
        {
            "pid": p["pid"],
            "name": p["name"],
            "cpu": round(p["cpu_percent"] or 0, 1),
            "mem": round(p["memory_percent"] or 0, 1),
            "status": p["status"],
        }
        for p in procs[:15]
    ]

    return {
        "cpu": {
            "percent": cpu_percent,
            "count": cpu_count,
            "freq_mhz": freq_mhz,
        },
        "ram": {
            "total_gb": round(ram.total / (1024 ** 3), 1),
            "used_gb": round(ram.used / (1024 ** 3), 1),
            "available_gb": round(ram.available / (1024 ** 3), 1),
            "percent": ram.percent,
        },
        "swap": {
            "total_gb": round(swap.total / (1024 ** 3), 1),
            "used_gb": round(swap.used / (1024 ** 3), 1),
            "percent": swap.percent,
        },
        "disk": disks,
        "net": {
            "bytes_sent_mb": round(net.bytes_sent / (1024 ** 2), 1),
            "bytes_recv_mb": round(net.bytes_recv / (1024 ** 2), 1),
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
        },
        "uptime_seconds": int(uptime_sec),
        "uptime_human": _uptime_human(uptime_sec),
        "processes": top_procs,
    }



@router.get("/usage", response_class=HTMLResponse)
async def usage_page(request: Request):
    """Render the server usage stats page."""
    return templates.TemplateResponse("pages/usage.html", {
        "request": request,
        "active_page": "usage",
    })


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Render the dashboard page with live stats."""
    nginx_status, pdns_status = await asyncio.gather(
        _check_nginx(), _check_powerdns()
    )

    domain_count = await db.scalar(select(func.count()).select_from(Domain))
    cert_count = await db.scalar(select(func.count()).select_from(SslCert))
    proxy_count = await db.scalar(select(func.count()).select_from(ReverseProxy))
    open_errors = await error_service.unresolved_count(db)

    return templates.TemplateResponse("pages/dashboard.html", {
        "request": request,
        "active_page": "dashboard",
        "server_ip": config.SERVER_IP,
        "hostname": socket.gethostname(),
        "nginx": nginx_status,
        "powerdns": pdns_status,
        "domain_count": domain_count or 0,
        "cert_count": cert_count or 0,
        "proxy_count": proxy_count or 0,
        "open_errors": open_errors,
    })
