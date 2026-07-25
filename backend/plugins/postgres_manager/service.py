"""
service.py — PostgreSQL service status, lifecycle hooks, and 30-second cache.

Handles installation detection, systemctl calls, and the usage-page hook.
All business logic (CRUD) lives in queries.py.

# TODO(v2): add remote connection profile support here — see docs/PHASE2_REMOTE.md
"""
import logging
import os
import shutil
import socket
import subprocess
import time
from typing import Any

try:
    import psutil as _psutil
    _PSUTIL = True
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _PSUTIL = False

logger = logging.getLogger(__name__)

_CACHE_TTL = 30  # seconds between real systemctl checks


class PostgresService:

    def __init__(self) -> None:
        self._status_cache: dict[str, Any] = {}
        self._cache_ts: float = 0.0

    # ------------------------------------------------------------------
    # Installation check
    # ------------------------------------------------------------------

    def is_installed(self) -> bool:
        """Return True if the psql client binary is present on this system."""
        return bool(shutil.which("psql") or os.path.exists("/usr/bin/psql"))

    # ------------------------------------------------------------------
    # Status (cached, 30-second TTL)
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return service status. Real probe runs at most once per 30 seconds."""
        if self._status_cache and (time.monotonic() - self._cache_ts) < _CACHE_TTL:
            return self._status_cache
        result = self._fetch_status()
        self._status_cache = result
        self._cache_ts = time.monotonic()
        return result

    def _fetch_status(self) -> dict[str, Any]:
        installed = self.is_installed()
        running = False
        version = ""
        pid: int | None = None
        ram_mb = 0.0
        port_open = self._check_port(5432)

        if installed and os.name != "nt":
            try:
                res = subprocess.run(
                    ["systemctl", "is-active", "postgresql"],
                    capture_output=True, text=True, timeout=5, shell=False,
                )
                running = res.stdout.strip() == "active"
            except Exception as exc:
                logger.warning("Could not query postgresql systemctl status: %s", exc)

            if running:
                pid = self._get_pid()
                ram_mb = self._get_ram_mb(pid)
                version = self._get_version()

        return {
            "installed": installed,
            "running": running,
            "version": version,
            "pid": pid,
            "ram_mb": ram_mb,
            "port_open": port_open,
            "mode": "local",  # TODO(v2): switch to "remote" when a profile is active
        }

    # ------------------------------------------------------------------
    # Usage page hook — contract: cpu, mem, memory, count, status
    # ------------------------------------------------------------------

    def get_usage(self) -> dict[str, Any]:
        """Return metrics consumed by the panel Usage page."""
        status = self.get_status()
        running = status.get("running", False)
        ram_mb = status.get("ram_mb", 0.0)
        return {
            "cpu": self._get_cpu_percent(status.get("pid")),
            "mem": round(ram_mb, 1),
            "memory": f"{ram_mb:.1f} MB",
            "count": 1 if running else 0,
            "status": "active" if running else "stopped",
        }

    # ------------------------------------------------------------------
    # Service control
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._systemctl("start")
        self._invalidate_cache()

    def stop(self) -> None:
        self._systemctl("stop")
        self._invalidate_cache()

    def restart(self) -> None:
        self._systemctl("restart")
        self._invalidate_cache()

    def _systemctl(self, action: str) -> None:
        if os.name == "nt":
            logger.info("[DEV] Mock systemctl %s postgresql", action)
            return
        subprocess.run(
            ["sudo", "-n", "systemctl", action, "postgresql"],
            check=True, timeout=30, shell=False,
        )

    # ------------------------------------------------------------------
    # Lifecycle hooks (plugin enable / disable)
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """Plugin disabled — flush cache so RAM footprint drops to zero."""
        self._invalidate_cache()
        logger.info("postgres_manager: paused, cache cleared.")

    def resume(self) -> None:
        """Plugin re-enabled — flush stale cache so next call fetches fresh state."""
        self._invalidate_cache()
        logger.info("postgres_manager: resumed.")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _invalidate_cache(self) -> None:
        self._status_cache = {}
        self._cache_ts = 0.0

    def _check_port(self, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                return s.connect_ex(("127.0.0.1", port)) == 0
        except Exception:
            return False

    def _get_pid(self) -> int | None:
        try:
            res = subprocess.run(
                ["pgrep", "-x", "postgres"],
                capture_output=True, text=True, timeout=5, shell=False,
            )
            pids = res.stdout.strip().split()
            return int(pids[0]) if pids else None
        except Exception:
            return None

    def _get_ram_mb(self, pid: int | None) -> float:
        if pid is None or not _PSUTIL:
            return 0.0
        try:
            return round(_psutil.Process(pid).memory_info().rss / 1_048_576, 1)
        except Exception:
            return 0.0

    def _get_cpu_percent(self, pid: int | None) -> float:
        if pid is None or not _PSUTIL:
            return 0.0
        try:
            return round(_psutil.Process(pid).cpu_percent(interval=0.1), 1)
        except Exception:
            return 0.0

    def _get_version(self) -> str:
        try:
            res = subprocess.run(
                ["psql", "--version"],
                capture_output=True, text=True, timeout=5, shell=False,
            )
            return res.stdout.strip().split("\n")[0]
        except Exception:
            return ""


# Module-level singleton — imported by router.py and other plugins
postgres_service = PostgresService()
