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
from pathlib import Path
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession



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
        if pid is None:
            return 0.0
        if _PSUTIL and _psutil is not None:
            try:
                return round(_psutil.Process(pid).memory_info().rss / 1_048_576, 1)
            except Exception:
                pass
        try:
            res = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5, shell=False,
            )
            rss_kb = float(res.stdout.strip() or 0)
            return round(rss_kb / 1024.0, 1)
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


    # ------------------------------------------------------------------
    # Remote Access & SSL Stream Proxy (SQLite DB Persistence)
    # ------------------------------------------------------------------

    async def list_remote_domains(self, db: AsyncSession | None = None) -> list[dict[str, Any]]:
        """Return list of configured remote access domains from SQLite DB."""
        if db is None:
            return []
        try:
            from sqlalchemy import select
            from models.postgres_remote import PostgresRemoteDomain
            res = await db.scalars(select(PostgresRemoteDomain).order_by(PostgresRemoteDomain.created_at.desc()))
            records = list(res.all())
            return [
                {
                    "domain": r.full_domain,
                    "mode": r.mode,
                    "ssl_active": r.ssl_active,
                    "nginx_stream": r.nginx_stream,
                }
                for r in records
            ]
        except Exception as exc:
            logger.warning("Failed to list remote domains from DB: %s", exc)
            return []

    async def add_remote_domain(
        self,
        db: AsyncSession,
        mode: str,
        domain: str | None,
        subdomain: str | None,
        hostname: str | None,
        issue_ssl: bool = True,
    ) -> dict[str, Any]:
        """Add a new remote domain endpoint to SQLite DB, issue SSL, and write Nginx stream proxy."""
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain
        from models.domain import Domain

        domain_id = None
        if mode == "managed":
            if not domain or not subdomain:
                raise ValueError("Parent domain and subdomain prefix are required.")
            full_host = f"{subdomain.strip().rstrip('.')}.{domain.strip().lstrip('.')}"
            parent = await db.scalar(select(Domain).where(Domain.name == domain.strip()))
            if parent:
                domain_id = parent.id
        else:
            if not hostname:
                raise ValueError("External hostname is required.")
            full_host = hostname.strip()

        # Validate domain format
        import re
        if not re.match(r"^[a-zA-Z0-9.\-]{3,253}$", full_host):
            raise ValueError(f"Invalid hostname format: {full_host}")

        existing = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if existing:
            raise ValueError(f"Domain '{full_host}' is already configured for remote access.")

        ssl_active = False
        if issue_ssl and os.name != "nt":
            ssl_active = self._issue_certbot_ssl(full_host)

        stream_written = self._write_nginx_stream_conf(full_host)

        record = PostgresRemoteDomain(
            domain_id=domain_id,
            mode=mode,
            subdomain=subdomain or "",
            full_domain=full_host,
            ssl_active=ssl_active,
            nginx_stream=stream_written or os.name == "nt",
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

        return {
            "domain": record.full_domain,
            "mode": record.mode,
            "ssl_active": record.ssl_active,
            "nginx_stream": record.nginx_stream,
        }

    async def reissue_remote_ssl(self, db: AsyncSession, full_host: str) -> dict[str, Any]:
        """Re-issue Let's Encrypt SSL certificate for a specific domain endpoint in SQLite DB."""
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain

        target = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if not target:
            raise ValueError(f"Domain '{full_host}' not found in remote access list.")

        ssl_active = False
        if os.name != "nt":
            ssl_active = self._issue_certbot_ssl(full_host)
            self._write_nginx_stream_conf(full_host)
        else:
            ssl_active = True

        target.ssl_active = ssl_active
        target.nginx_stream = True
        await db.commit()
        await db.refresh(target)

        return {
            "domain": target.full_domain,
            "mode": target.mode,
            "ssl_active": target.ssl_active,
            "nginx_stream": target.nginx_stream,
        }

    async def delete_remote_domain(self, db: AsyncSession, full_host: str) -> bool:
        """Remove a remote access domain from SQLite DB and remove its Nginx stream config."""
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain

        target = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if not target:
            raise ValueError(f"Domain '{full_host}' not found.")

        if os.name != "nt":
            conf_path = Path(f"/etc/nginx/streams.d/postgres_{full_host}.conf")
            try:
                subprocess.run(["sudo", "-n", "rm", "-f", str(conf_path)], check=False, timeout=10)
                subprocess.run(["sudo", "-n", "systemctl", "reload", "nginx"], check=False, timeout=15)
            except Exception as exc:
                logger.warning("Failed to remove Nginx stream config for %s: %s", full_host, exc)

        await db.delete(target)
        await db.commit()
        return True

    def get_remote_status(self) -> dict[str, Any]:
        """Backward-compatible remote status helper."""
        return {"enabled": False, "domain": None, "ssl_active": False, "nginx_stream": False}


    def _issue_certbot_ssl(self, full_host: str) -> bool:
        """Helper to run Certbot webroot challenge and check cert existence via sudo safely."""
        try:
            subprocess.run(
                ["sudo", "-n", "certbot", "certonly", "--webroot", "-w", "/var/www/acme-challenge",
                 "-d", full_host, "--non-interactive", "--agree-tos", "--register-unsafely-without-email"],
                capture_output=True, text=True, timeout=90, shell=False,
            )
        except Exception as exc:
            logger.warning("Certbot issue attempt for %s: %s", full_host, exc)

        if os.name == "nt":
            return True

        try:
            res = subprocess.run(
                ["sudo", "-n", "test", "-f", f"/etc/letsencrypt/live/{full_host}/fullchain.pem"],
                check=False, timeout=5, shell=False,
            )
            return res.returncode == 0
        except Exception:
            return False


    def _write_nginx_stream_conf(self, full_host: str) -> bool:
        """Helper to write Nginx TCP stream proxy configuration file."""
        if os.name == "nt":
            return True
        stream_dir = Path("/etc/nginx/streams.d")
        try:
            subprocess.run(["sudo", "-n", "mkdir", "-p", str(stream_dir)], check=True, timeout=10)
            conf_content = f"""# Managed by srv-panel postgres_manager plugin
stream {{
    upstream postgres_backend_{full_host.replace('.', '_')} {{
        server 127.0.0.1:5432;
    }}
    server {{
        listen 5432 ssl;
        ssl_certificate /etc/letsencrypt/live/{full_host}/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/{full_host}/privkey.pem;
        proxy_pass postgres_backend_{full_host.replace('.', '_')};
    }}
}}
"""
            conf_path = stream_dir / f"postgres_{full_host}.conf"
            proc = subprocess.run(
                ["sudo", "-n", "tee", str(conf_path)],
                input=conf_content, text=True, capture_output=True, timeout=10,
            )
            if proc.returncode == 0:
                subprocess.run(["sudo", "-n", "systemctl", "reload", "nginx"], check=False, timeout=15)
                return True
        except Exception as exc:
            logger.warning("Nginx stream config creation failed for %s: %s", full_host, exc)
        return False

    def enable_remote(
        self,
        mode: str,
        domain: str | None,
        subdomain: str | None,
        hostname: str | None,
        issue_ssl: bool = True,
    ) -> dict[str, Any]:
        """Legacy enable_remote call wrapper."""
        return self.add_remote_domain(mode, domain, subdomain, hostname, issue_ssl)

    def disable_remote(self) -> dict[str, Any]:
        """Legacy disable_remote call wrapper."""
        domains = self.list_remote_domains()
        for d in domains:
            if d.get("domain"):
                self.delete_remote_domain(d["domain"])
        return {"enabled": False, "domain": None, "ssl_active": False, "nginx_stream": False}



# Module-level singleton — imported by router.py and other plugins
postgres_service = PostgresService()
postgres_manager_service = postgres_service


