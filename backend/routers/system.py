"""
routers/system.py — Health check + server status routes
Returns real nginx and PowerDNS status, not mocked data.
"""
import asyncio
from copy import deepcopy
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
from services import dependency_usage_service
from services import hosted_app_usage_service
from services import php_site_usage_service
from services import process_usage_classifier
from services import plugin_usage_service
from services import container_app_usage_service
from services import server_control_service
from services.resource_guard_service import resource_guard_service
from services.task_manager_service import task_manager_service
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

    # 2. zRAM inspection without spawning systemctl on every stats request.
    zram_active = False
    try:
        swaps = Path("/proc/swaps")
        if swaps.is_file():
            zram_active = any(
                line.split()[0].startswith("/dev/zram")
                for line in swaps.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]
                if line.split()
            )
        if zram_active:
            opt_active = True
    except OSError:
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
        snap_root = Path("/var/lib/snapd/snaps")
        ignored_snaps = {"core", "core18", "core20", "core22", "bare", "snapd", "lxd"}
        if snap_root.is_dir():
            try:
                has_snaps = any(
                    item.stem.rsplit("_", 1)[0] not in ignored_snaps
                    for item in snap_root.glob("*.snap")
                )
            except OSError:
                pass
        
        _HARDWARE_CACHE = {
            "has_fibre": has_fibre,
            "has_modem": has_modem,
            "has_snaps": has_snaps
        }

    # 5. Managed disk swap inspection. A safe resize can retain a versioned
    # /swapfile path when Linux will not rename an active swapfile.
    swapfile_size_mb = 0
    try:
        swaps = Path("/proc/swaps")
        if swaps.is_file():
            for line in swaps.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
                fields = line.split()
                if len(fields) >= 3 and fields[0].startswith("/swapfile"):
                    swapfile_size_mb += round(int(fields[2]) / 1024)
    except (OSError, ValueError):
        pass

    can_purge_swap = False
    can_disable_swap = True
    ram_available_mb = 0
    swap_used_mb = 0
    total_swap_mb = 0
    try:
        if _psutil is not None:
            vm = _psutil.virtual_memory()
            sm = _psutil.swap_memory()
            total_swap_mb = round(sm.total / (1024 * 1024))
            ram_available_mb = round(vm.available / (1024 * 1024))
            swap_used_mb = round(sm.used / (1024 * 1024))
            can_purge_swap = sm.used == 0 or vm.available >= (sm.used + 100 * 1024 * 1024)
            can_disable_swap = sm.used == 0 or vm.available >= (sm.used + 100 * 1024 * 1024)
    except Exception:
        pass

    return {
        "optimization_active": opt_active,
        "zram_active": zram_active,
        "nginx_single_worker": nginx_single,
        "nginx_worker_setting": worker_setting,
        "advanced_active": advanced_active,
        "advanced_tuning_active": advanced_active,
        "hardware_checks": _HARDWARE_CACHE,
        "swapfile_size_mb": swapfile_size_mb,
        "total_swap_mb": total_swap_mb,
        "can_safely_purge_swap": can_purge_swap,
        "can_safely_disable_swap": can_disable_swap,
        "ram_available_mb": ram_available_mb,
        "swap_used_mb": swap_used_mb,
    }


@router.get("/api/system/optimization/status")
async def optimization_status():
    """Return only optimization state so the UI can confirm a slow apply."""
    return await _get_optimization_status()


import threading

_proc_cpu_tracker: dict[int, tuple[float, float, float]] = {}
_proc_tracker_lock = threading.Lock()


def _collect_usage_snapshot() -> dict:
    """Collect synchronous psutil data away from the async request loop."""
    global _proc_cpu_tracker
    now = time.time()
    next_tracker: dict[int, tuple[float, float, float]] = {}

    with _proc_tracker_lock:
        prev_tracker = dict(_proc_cpu_tracker)

    cpu_percent = _psutil.cpu_percent(interval=None)
    cpu_count = _psutil.cpu_count(logical=True)
    cpu_freq = _psutil.cpu_freq()
    freq_mhz = round(cpu_freq.current) if cpu_freq else None

    # RAM
    ram = _psutil.virtual_memory()

    # Swap
    swap = _psutil.swap_memory()

    # Disk — all mounted partitions (filtered to real storage, skip pseudo/overlay that duplicate root in df)
    disks = []
    for part in _psutil.disk_partitions(all=False):
        try:
            # Skip Docker overlay layers and tmpfs pseudo mounts that make df output confusing
            # (overlay, tmpfs, squashfs etc duplicate / and clutter the UI)
            fst = (part.fstype or "").lower()
            dev = (part.device or "")
            mnt = part.mountpoint or ""
            if fst in ("tmpfs", "devtmpfs", "squashfs", "overlay", "nsfs", "cgroup", "cgroup2", "efivarfs"):
                continue
            if dev in ("tmpfs", "overlay", "none", "udev"):
                continue
            if mnt.startswith("/var/lib/docker/"):
                continue
            if mnt.startswith("/run/credentials"):
                continue
            # Extra guard: overlay device with overlay fstype
            if dev == "overlay" or fst == "overlay":
                continue
            usage = _psutil.disk_usage(mnt)
            disks.append({
                "mount": mnt,
                "device": dev,
                "fstype": part.fstype,
                "total_gb": round(usage.total / (1024 ** 3), 1),
                "used_gb": round(usage.used / (1024 ** 3), 1),
                "free_gb": round(usage.free / (1024 ** 3), 1),
                "percent": usage.percent,
            })
        except (PermissionError, FileNotFoundError, OSError):
            pass
    # Fallback: if filtering removed everything (unlikely on minimal VPS), show root
    if not disks:
        try:
            usage = _psutil.disk_usage("/")
            disks.append({
                "mount": "/",
                "device": "/dev/vda2",
                "fstype": "ext4",
                "total_gb": round(usage.total / (1024 ** 3), 1),
                "used_gb": round(usage.used / (1024 ** 3), 1),
                "free_gb": round(usage.free / (1024 ** 3), 1),
                "percent": usage.percent,
            })
        except Exception:
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
                  count=0, worker_count=0, status="stopped", _memory_bytes=0)
        for key, label in service_labels.items()
    }
    for p in _psutil.process_iter(
        [
            "pid",
            "name",
            "cmdline",
            "cpu_times",
            "create_time",
            "memory_percent",
            "memory_info",
            "status",
            "username",
        ]
    ):
        try:
            info = p.info
            pid = info["pid"]
            times = info.get("cpu_times")
            create_time = float(info.get("create_time") or 0.0)
            total_time = float(times.user + times.system) if times else 0.0

            if pid in prev_tracker and abs(prev_tracker[pid][0] - create_time) < 1.0:
                _, prev_total, prev_time = prev_tracker[pid]
                dt = now - prev_time
                if dt > 0.2:
                    cpu_pct = max(0.0, ((total_time - prev_total) / dt) * 100.0)
                else:
                    cpu_pct = 0.0
            else:
                dt = now - create_time if create_time and (now - create_time > 0.5) else 0.0
                cpu_pct = max(0.0, (total_time / dt) * 100.0) if dt > 0 else 0.0

            info["cpu_percent"] = round(cpu_pct, 1)
            next_tracker[pid] = (create_time, total_time, now)
            procs.append(info)

            name = info.get("name", "").lower() if info.get("name") else ""
            cmdline = " ".join(info.get("cmdline") or []).lower()

            svc = process_usage_classifier.stack_service(name, cmdline)

            if svc:
                services[svc]["cpu"] += info["cpu_percent"] or 0.0
                services[svc]["_memory_bytes"] += int(
                    getattr(info.get("memory_info"), "rss", 0) or 0
                )
                services[svc]["count"] += 1
                services[svc]["status"] = "running"
                if svc == "nginx" and process_usage_classifier.is_nginx_worker(cmdline):
                    services[svc]["worker_count"] += 1
        except (_psutil.NoSuchProcess, _psutil.AccessDenied):
            pass

    with _proc_tracker_lock:
        _proc_cpu_tracker = next_tracker

    return {
        "cpu_percent": cpu_percent,
        "cpu_count": cpu_count,
        "freq_mhz": freq_mhz,
        "ram": ram,
        "swap": swap,
        "disks": disks,
        "net": net,
        "uptime_sec": uptime_sec,
        "procs": procs,
        "services": services,
    }


_STATS_CACHE_SECONDS = 15.0
_stats_cache: dict | None = None
_stats_cache_at = 0.0
_stats_cache_lock = asyncio.Lock()


async def _build_server_stats(db: AsyncSession) -> dict:
    """Live server resource stats via psutil — CPU, RAM, disk, network, processes."""
    from fastapi import HTTPException
    if not _PSUTIL_OK:
        raise HTTPException(
            status_code=503,
            detail="psutil not installed. Run: pip install psutil==6.0.0",
        )

    snapshot = await asyncio.to_thread(_collect_usage_snapshot)
    cpu_percent = snapshot["cpu_percent"]
    cpu_count = snapshot["cpu_count"]
    freq_mhz = snapshot["freq_mhz"]
    ram = snapshot["ram"]
    swap = snapshot["swap"]
    disks = snapshot["disks"]
    net = snapshot["net"]
    uptime_sec = snapshot["uptime_sec"]
    procs = snapshot["procs"]
    services = snapshot["services"]

    docker_status = await asyncio.to_thread(
        dependency_manager.get_status, "docker", cached=True
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

    plugins = await plugin_usage_service.get_plugin_usage(
        procs,
        ram.total,
        live_hooks=False,
    )
    hosted_apps = await hosted_app_usage_service.get_usage(db, procs, ram.total)
    php_sites = await php_site_usage_service.get_usage(db, procs, ram.total)
    container_apps = await container_app_usage_service.get_usage(db, ram.total)
    dependencies = await dependency_usage_service.get_runtime_usage(procs, ram.total)
    resource_guard = await resource_guard_service.status(db)
    if "railpack_apps" in plugins:
        plugins["railpack_apps"].update(container_apps["total"])

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
            # Keep the displayed GB value aligned with psutil's percent
            # calculation, which is based on total minus available memory.
            "used_gb": round((ram.total - ram.available) / (1024 ** 3), 1),
            "available_gb": round(ram.available / (1024 ** 3), 1),
            "percent": ram.percent,
            "is_low_ram": is_low_ram,
        },
        "swap": {
            "total_gb": round(swap.total / (1024 ** 3), 1),
            "used_gb": round(swap.used / (1024 ** 3), 1),
            "percent": swap.percent,
            "swapfile_size_mb": opt_status.get("swapfile_size_mb", 0),
            "zram_active": opt_status.get("zram_active", False),
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
        "dependencies": dependencies,
        "hosted_apps": hosted_apps,
        "php_sites": php_sites,
        "container_apps": container_apps,
        "resource_guard": resource_guard,
        "processes": top_procs,
        "optimization": opt_status,
    }


def _invalidate_stats_cache():
    global _stats_cache
    _stats_cache = None


@router.get("/api/stats")
async def server_stats(force: bool = False, db: AsyncSession = Depends(get_db)):
    """Return one shared short-lived stats snapshot for all browser sessions."""
    global _stats_cache, _stats_cache_at
    now = time.monotonic()
    if not force and _stats_cache is not None and now - _stats_cache_at < _STATS_CACHE_SECONDS:
        return deepcopy(_stats_cache)

    async with _stats_cache_lock:
        now = time.monotonic()
        if not force and _stats_cache is not None and now - _stats_cache_at < _STATS_CACHE_SECONDS:
            return deepcopy(_stats_cache)
        payload = await _build_server_stats(db)
        _stats_cache = deepcopy(payload)
        _stats_cache_at = time.monotonic()
        return payload


def _get_optimize_script_path() -> Path | None:
    for candidate in [
        config.BASE_DIR / "scripts" / "optimize.sh",
        config.BASE_DIR.parent / "scripts" / "optimize.sh",
        Path("/opt/srv-panel/scripts/optimize.sh"),
    ]:
        if candidate.exists():
            return candidate
    return None


class OptimizationToggleIn(BaseModel):
    enabled: bool


class NginxWorkerToggleIn(BaseModel):
    single_worker: bool


class AdvancedTuningToggleIn(BaseModel):
    enabled: bool


@router.post("/api/system/optimization/toggle")
async def toggle_optimization(payload: OptimizationToggleIn):
    """Enable or disable server Low-RAM optimization mode."""
    script_path = _get_optimize_script_path()
    if not script_path:
        return {"success": False, "detail": "optimize.sh script not found"}

    action = "enable" if payload.enabled else "disable"
    res = await run(["bash", str(script_path), action])
    _invalidate_stats_cache()
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
    script_path = _get_optimize_script_path()
    if not script_path:
        return {"success": False, "detail": "optimize.sh script not found"}

    action = "nginx-worker-1" if payload.single_worker else "nginx-worker-auto"
    res = await run(["bash", str(script_path), action])
    _invalidate_stats_cache()
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
    script_path = _get_optimize_script_path()
    if not script_path:
        return {"success": False, "detail": "optimize.sh script not found"}

    action = "advanced-enable" if payload.enabled else "advanced-disable"
    res = await run(["bash", str(script_path), action])
    _invalidate_stats_cache()
    detail = res.stdout if res.success else res.stderr
    if "password is required" in detail.lower():
        detail = "Sudoers permissions need updating. Please run on server: sudo bash /opt/srv-panel/scripts/update.sh"
    return {
        "success": res.success,
        "detail": detail,
    }


class SwapConfigIn(BaseModel):
    size_mb: int


@router.post("/api/system/swap/set")
async def set_swap_size(payload: SwapConfigIn):
    """Configure or resize /swapfile (0 to disable, or size in MB)."""
    if payload.size_mb < 0 or payload.size_mb > 32768:
        return {"success": False, "detail": "Swap size must be between 0 and 32768 MB."}

    script_path = _get_optimize_script_path()
    if not script_path:
        return {"success": False, "detail": "optimize.sh script not found"}

    res = await run(["bash", str(script_path), "set-swap", str(payload.size_mb)])
    _invalidate_stats_cache()
    detail = res.stdout if res.success else res.stderr
    if "password is required" in detail.lower():
        detail = "Sudoers permissions need updating. Please run on server: sudo bash /opt/srv-panel/scripts/update.sh"
    return {
        "success": res.success,
        "detail": detail.strip(),
    }


@router.post("/api/system/memory/clean-ram")
async def clean_ram_cache():
    """Safely drop inactive kernel pagecaches and compact memory."""
    script_path = _get_optimize_script_path()
    if not script_path:
        return {"success": False, "detail": "optimize.sh script not found"}

    res = await run(["bash", str(script_path), "clean-ram"])
    _invalidate_stats_cache()
    await task_manager_service.record_completed_task(
        category="system",
        action="clean_ram",
        target_id="memory",
        label="Clean System RAM",
        success=res.success,
        message="RAM cache cleaned successfully" if res.success else "Failed to clean RAM cache",
    )
    if res.success:
        try:
            data = json.loads(res.stdout)
            return data
        except Exception:
            return {"success": True, "detail": res.stdout.strip()}
    return {"success": False, "detail": res.stderr.strip() or "Failed to clean RAM caches"}


@router.post("/api/system/memory/clean-swap")
async def clean_swap_cache():
    """Smart swap cleaner with OOM safety protection."""
    script_path = _get_optimize_script_path()
    if not script_path:
        return {"success": False, "detail": "optimize.sh script not found"}

    res = await run(["bash", str(script_path), "clean-swap"])
    _invalidate_stats_cache()
    await task_manager_service.record_completed_task(
        category="system",
        action="clean_swap",
        target_id="swap",
        label="Clean System Swap",
        success=res.success,
        message="Swap cleaned successfully" if res.success else "Failed to purge swap",
    )
    if res.success:
        try:
            data = json.loads(res.stdout)
            return data
        except Exception:
            return {"success": True, "detail": res.stdout.strip()}
    return {"success": False, "detail": res.stderr.strip() or "Failed to purge swap"}


@router.post("/api/system/reboot")
async def reboot_server():
    """Reboot the entire server/VPS."""
    try:
        await server_control_service.request_reboot()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"success": True, "detail": "Server reboot accepted. The system will restart shortly."}




@router.get("/usage", response_class=HTMLResponse)
async def usage_page(request: Request):
    """Render the server usage stats page."""
    return templates.TemplateResponse("pages/usage/index.html", {
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
