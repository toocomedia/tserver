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

import json
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from database import get_db
from dependencies import dependency_manager
from models.domain import Domain
from models.ssl_cert import SslCert
from models.proxy import ReverseProxy
from services import error_service
from services import plugin_usage_service
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


_HARDWARE_CACHE = None

async def _get_optimization_status() -> dict:
    """Inspect system optimization state in pure Python for high reliability."""
    import re
    
    # 1. Swappiness / sysctl inspection
    opt_active = Path("/etc/sysctl.d/99-srv-panel-optimize.conf").exists()
    if not opt_active and Path("/proc/sys/vm/swappiness").exists():
        try:
            val = Path("/proc/sys/vm/swappiness").read_text().strip()
            if val == "10":
                opt_active = True
        except Exception:
            pass

    # 2. zRAM inspection
    zram_active = False
    try:
        res = await run(["systemctl", "is-active", "zramswap"])
        if res.success and "active" in res.stdout.strip():
            zram_active = True
            opt_active = True
    except Exception:
        pass

    # 3. Nginx worker_processes inspection
    nginx_single = False
    worker_setting = "auto"
    nginx_conf = Path("/etc/nginx/nginx.conf")
    if nginx_conf.exists():
        try:
            content = nginx_conf.read_text()
            match = re.search(r'worker_processes\s+([^;]+);', content)
            if match:
                worker_setting = match.group(1).strip()
                if worker_setting == "1":
                    nginx_single = True
        except Exception:
            pass

    # 4. Advanced Server Tuning Inspection
    advanced_active = Path("/etc/systemd/journald.conf.d/99-srv-panel.conf").exists()

    # Hardware Caching
    global _HARDWARE_CACHE
    if _HARDWARE_CACHE is None:
        has_fibre = Path("/sys/class/fc_host").exists()
        
        has_modem = False
        try:
            net_dir = Path("/sys/class/net")
            if net_dir.exists():
                for p in net_dir.iterdir():
                    if p.name.startswith("wwan"):
                        has_modem = True
                        break
        except Exception:
            pass

        has_snaps = False
        if Path("/usr/bin/snap").exists():
            snap_res = await run(["snap", "list"])
            if snap_res.success:
                lines = snap_res.stdout.strip().split("\n")[1:]
                for line in lines:
                    parts = line.split()
                    if parts:
                        name = parts[0]
                        if name not in ["core", "core18", "core20", "core22", "bare", "snapd", "lxd"]:
                            has_snaps = True
                            break
        
        _HARDWARE_CACHE = {
            "has_fibre": has_fibre,
            "has_modem": has_modem,
            "has_snaps": has_snaps
        }

    return {
        "optimization_active": opt_active,
        "zram_active": zram_active,
        "nginx_single_worker": nginx_single,
        "nginx_worker_setting": worker_setting,
        "advanced_active": advanced_active,
        "hardware_checks": _HARDWARE_CACHE,
    }


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

    # Top 15 processes by CPU usage & Stack Services
    procs = []
    service_labels = {
        "nginx": "Nginx",
        "powerdns": "PowerDNS",
        "panel": "Panel (FastAPI)",
        "docker": "Docker Engine",
    }
    services = {
        key: dict(label=label, cpu=0.0, mem=0.0, memory="0 MB",
                  count=0, status="stopped", _memory_bytes=0)
        for key, label in service_labels.items()
    }
    for p in _psutil.process_iter(
        [
            "pid",
            "name",
            "cmdline",
            "cpu_percent",
            "memory_percent",
            "memory_info",
            "status",
        ]
    ):
        try:
            info = p.info
            if info["cpu_percent"] is not None:
                procs.append(info)

                name = info.get("name", "").lower() if info.get("name") else ""
                cmdline = " ".join(info.get("cmdline") or []).lower()

                svc = None
                if "nginx" in name:
                    svc = "nginx"
                elif "pdns_server" in name:
                    svc = "powerdns"
                elif "python" in name or "uvicorn" in name:
                    if "srv-panel" in cmdline or "main.py" in cmdline or "uvicorn" in cmdline:
                        svc = "panel"
                elif name in {"dockerd", "containerd", "docker-proxy"}:
                    svc = "docker"

                if svc:
                    services[svc]["cpu"] += info["cpu_percent"] or 0.0
                    services[svc]["_memory_bytes"] += int(
                        getattr(info.get("memory_info"), "rss", 0) or 0
                    )
                    services[svc]["count"] += 1
                    services[svc]["status"] = "running"
        except (_psutil.NoSuchProcess, _psutil.AccessDenied):
            pass

    docker_status = await asyncio.to_thread(
        dependency_manager.get_status, "docker"
    )
    if docker_status:
        if docker_status.get("healthy"):
            services["docker"]["status"] = "running"
        elif not docker_status.get("installed"):
            services["docker"]["status"] = "missing"
        elif not docker_status.get("desired_enabled", True):
            services["docker"]["status"] = "disabled"
        else:
            services["docker"]["status"] = "stopped"

    for s in services.values():
        s["cpu"] = round(s["cpu"], 1)
        memory_bytes = int(s.pop("_memory_bytes"))
        s["mem"] = round((memory_bytes / ram.total) * 100, 1)
        s["memory"] = (
            f"{memory_bytes / (1024 ** 2):.0f} MB "
            f"({s['mem']:.1f}% of server)"
        )

    plugins = await plugin_usage_service.get_plugin_usage(procs, ram.total)

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

    opt_status = await _get_optimization_status()
    is_low_ram = ram.total < (2.0 * 1024 ** 3)

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
            "is_low_ram": is_low_ram,
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
        "services": services,
        "plugins": plugins,
        "processes": top_procs,
        "optimization": opt_status,
    }


class OptimizationToggleIn(BaseModel):
    enabled: bool


class NginxWorkerToggleIn(BaseModel):
    single_worker: bool


class AdvancedTuningToggleIn(BaseModel):
    enabled: bool


@router.post("/api/system/optimization/toggle")
async def toggle_optimization(payload: OptimizationToggleIn):
    """Enable or disable server Low-RAM optimization mode."""
    script_path = config.BASE_DIR / "scripts" / "optimize.sh"
    if not script_path.exists():
        script_path = Path("/opt/srv-panel/scripts/optimize.sh")

    if not script_path.exists():
        return {"success": False, "detail": "optimize.sh script not found"}

    action = "enable" if payload.enabled else "disable"
    res = await run(["bash", str(script_path), action])
    detail = res.stdout if res.success else res.stderr
    if "password is required" in detail.lower():
        detail = "Sudoers permissions need updating. Please run on server: sudo bash /opt/srv-panel/scripts/update.sh"
    return {
        "success": res.success,
        "detail": detail,
    }


@router.post("/api/system/nginx-worker/toggle")
async def toggle_nginx_worker(payload: NginxWorkerToggleIn):
    """Set Nginx worker_processes to 1 or auto independently."""
    script_path = config.BASE_DIR / "scripts" / "optimize.sh"
    if not script_path.exists():
        script_path = Path("/opt/srv-panel/scripts/optimize.sh")

    if not script_path.exists():
        return {"success": False, "detail": "optimize.sh script not found"}

    action = "nginx-worker-1" if payload.single_worker else "nginx-worker-auto"
    res = await run(["bash", str(script_path), action])
    detail = res.stdout if res.success else res.stderr
    if "password is required" in detail.lower():
        detail = "Sudoers permissions need updating. Please run on server: sudo bash /opt/srv-panel/scripts/update.sh"
    return {
        "success": res.success,
        "detail": detail,
    }


@router.post("/api/system/advanced/toggle")
async def toggle_advanced_tuning(payload: AdvancedTuningToggleIn):
    """Enable or disable Advanced Server Tuning."""
    script_path = config.BASE_DIR / "scripts" / "optimize.sh"
    if not script_path.exists():
        script_path = Path("/opt/srv-panel/scripts/optimize.sh")

    if not script_path.exists():
        return {"success": False, "detail": "optimize.sh script not found"}

    action = "advanced-enable" if payload.enabled else "advanced-disable"
    res = await run(["bash", str(script_path), action])
    detail = res.stdout if res.success else res.stderr
    if "password is required" in detail.lower():
        detail = "Sudoers permissions need updating. Please run on server: sudo bash /opt/srv-panel/scripts/update.sh"
    return {
        "success": res.success,
        "detail": detail,
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
