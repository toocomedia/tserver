"""
plugins/domain_analytics/service.py — Domain Analytics service lifecycle & background worker.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

import config
from plugins.domain_analytics.db import init_db, get_db
from plugins.domain_analytics.log_parser import parse_log_file
from plugins.domain_analytics.aggregator import process_domain_entries, prune_old_data
from plugins.domain_analytics.geoip_service import geoip_service
from plugins.domain_analytics.queries import fetch_domains_summary, fetch_domain_detail

logger = logging.getLogger(__name__)


class DomainAnalyticsService:
    plugin_id = "domain_analytics"

    def __init__(self):
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        init_db()
        # Clean out legacy invalid log paths
        with get_db() as conn:
            conn.execute("UPDATE tracked_domains SET log_path = '' WHERE log_path LIKE '%/php-sites/%' OR log_path = '/var/log/nginx/access.log'")

    def is_installed(self) -> bool:
        return True

    async def start(self) -> None:
        """Start background worker task if not already running."""
        if self._running and self._worker_task and not self._worker_task.done():
            return
        self._running = True
        self._worker_task = asyncio.create_task(self.run_worker_loop())
        logger.info("Domain analytics service worker started.")

    async def stop(self) -> None:
        """Stop background worker task immediately."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("Domain analytics service worker stopped.")

    async def pause(self) -> None:
        await self.stop()

    async def resume(self) -> None:
        await self.start()

    def get_status(self) -> dict:
        with get_db() as conn:
            active_count = conn.execute("SELECT COUNT(*) as c FROM tracked_domains WHERE is_active = 1").fetchone()["c"]
            total_count = conn.execute("SELECT COUNT(*) as c FROM tracked_domains").fetchone()["c"]
            total_requests = conn.execute("SELECT SUM(total_requests) as s FROM hourly_stats").fetchone()["s"] or 0
        return {
            "installed": True,
            "running": self._running,
            "active_domains": active_count,
            "total_domains": total_count,
            "total_recorded_requests": total_requests,
            "geoip": geoip_service.get_settings(),
        }

    def resolve_domain_log_path(self, domain_name: str) -> str:
        """Find the appropriate Nginx access log path for the domain."""
        domain_safe = domain_name.replace(":", "_")
        domain_no_port = domain_name.split(":")[0]

        log_dirs = [
            Path(getattr(config, "NGINX_LOG_DIR", "/var/log/nginx")),
            Path("/var/log/nginx"),
            Path("/opt/nginx/logs"),
            Path("/opt/openresty/nginx/logs"),
            Path("/usr/local/nginx/logs"),
        ]

        candidates = []
        for ldir in log_dirs:
            candidates.append(ldir / "domains" / f"{domain_safe}.access.log")
            candidates.append(ldir / f"{domain_safe}.access.log")
            candidates.append(ldir / f"{domain_safe}-access.log")
            if domain_safe != domain_no_port:
                candidates.append(ldir / "domains" / f"{domain_no_port}.access.log")
                candidates.append(ldir / f"{domain_no_port}.access.log")
                candidates.append(ldir / f"{domain_no_port}-access.log")

        candidates.append(Path(config.NGINX_WEBROOT) / domain_safe / "logs" / "access.log")
        candidates.append(Path(config.NGINX_WEBROOT) / domain_safe / "access.log")
        if domain_safe != domain_no_port:
            candidates.append(Path(config.NGINX_WEBROOT) / domain_no_port / "logs" / "access.log")
            candidates.append(Path(config.NGINX_WEBROOT) / domain_no_port / "access.log")

        for c in candidates:
            if c.exists() and c.is_file():
                return str(c)

        return str(candidates[0])

    def clear_domain_data(self, domain_name: str) -> None:
        """Wipe all historical stats and advance tracking offset to current end of file."""
        with get_db() as conn:
            d = conn.execute("SELECT log_path FROM tracked_domains WHERE domain_name = ?", (domain_name,)).fetchone()
            saved_path = d["log_path"] if d else ""
            log_path = Path(saved_path) if saved_path and Path(saved_path).exists() else Path(self.resolve_domain_log_path(domain_name))

            curr_offset = 0
            curr_inode = 0
            if log_path.exists() and log_path.is_file():
                st = log_path.stat()
                curr_offset = st.st_size
                curr_inode = st.st_ino

            conn.execute("DELETE FROM hourly_stats WHERE domain_name = ?", (domain_name,))
            conn.execute("DELETE FROM daily_visitors WHERE domain_name = ?", (domain_name,))
            conn.execute("DELETE FROM top_paths WHERE domain_name = ?", (domain_name,))
            conn.execute("DELETE FROM top_referrers WHERE domain_name = ?", (domain_name,))
            conn.execute("DELETE FROM error_logs WHERE domain_name = ?", (domain_name,))
            conn.execute("DELETE FROM geo_stats WHERE domain_name = ?", (domain_name,))
            conn.execute(
                "UPDATE tracked_domains SET last_offset = ?, last_inode = ?, log_path = ? WHERE domain_name = ?",
                (curr_offset, curr_inode, str(log_path), domain_name),
            )
        logger.info("Cleared analytics data for domain %s (offset advanced to %d)", domain_name, curr_offset)

    def process_domain_log(self, domain_name: str, from_beginning: bool = False, force: bool = False) -> dict:
        """Process logs for a specific domain immediately. Respects is_active unless force=True."""
        with get_db() as conn:
            d = conn.execute("SELECT * FROM tracked_domains WHERE domain_name = ?", (domain_name,)).fetchone()
            if not force and d and d["is_active"] == 0:
                return {
                    "success": False,
                    "log_path": d["log_path"] or "",
                    "file_exists": Path(d["log_path"]).exists() if d["log_path"] else False,
                    "processed_lines": 0,
                    "message": f"Tracking is paused for {domain_name}",
                }
            last_offset = 0 if from_beginning else (d["last_offset"] if d else 0)
            last_inode = d["last_inode"] if d else 0
            saved_path_str = d["log_path"] if d else ""

        if saved_path_str:
            log_path = Path(saved_path_str)
            if not log_path.exists():
                log_path = Path(self.resolve_domain_log_path(domain_name))
        else:
            log_path = Path(self.resolve_domain_log_path(domain_name))

        if not log_path.exists() or not log_path.is_file():
            return {
                "success": False,
                "log_path": str(log_path),
                "file_exists": False,
                "processed_lines": 0,
                "message": f"Log file not found at {log_path}",
            }

        entries, new_offset, new_inode = parse_log_file(log_path, last_offset=last_offset, last_inode=last_inode)
        if entries:
            process_domain_entries(domain_name, entries)

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            conn.execute("""
                INSERT INTO tracked_domains (domain_name, is_active, log_path, last_offset, last_inode, last_parsed_at)
                VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(domain_name) DO UPDATE SET
                    log_path = excluded.log_path,
                    last_offset = excluded.last_offset,
                    last_inode = excluded.last_inode,
                    last_parsed_at = excluded.last_parsed_at;
            """, (domain_name, str(log_path), new_offset, new_inode, now_str))

        return {
            "success": True,
            "log_path": str(log_path),
            "file_exists": True,
            "file_size_bytes": log_path.stat().st_size if log_path.exists() else 0,
            "processed_lines": len(entries),
            "message": f"Successfully parsed {len(entries)} lines from {log_path.name}",
        }

    def sync_domains_from_panel(self, panel_domains: List[str]) -> None:
        """Synchronize hosted domains into tracked_domains table."""
        with get_db() as conn:
            for domain in panel_domains:
                default_log = self.resolve_domain_log_path(domain)
                conn.execute("""
                    INSERT OR IGNORE INTO tracked_domains (domain_name, is_active, log_path)
                    VALUES (?, 0, ?);
                """, (domain, default_log))

    def toggle_domain(self, domain_name: str, is_active: bool, log_path: Optional[str] = None) -> bool:
        """Enable or disable analytics tracking for a domain."""
        resolved_log = log_path or self.resolve_domain_log_path(domain_name)
        with get_db() as conn:
            conn.execute("""
                INSERT INTO tracked_domains (domain_name, is_active, log_path)
                VALUES (?, ?, ?)
                ON CONFLICT(domain_name) DO UPDATE SET
                    is_active = excluded.is_active,
                    log_path = COALESCE(excluded.log_path, tracked_domains.log_path);
            """, (domain_name, 1 if is_active else 0, resolved_log))
        logger.info("Domain %s tracking set to %s", domain_name, is_active)
        return True

    def get_domain_active_status(self, domain_name: str) -> bool:
        with get_db() as conn:
            row = conn.execute("SELECT is_active FROM tracked_domains WHERE domain_name = ?", (domain_name,)).fetchone()
            return bool(row and row["is_active"] == 1)

    def list_domains_summary(self) -> List[Dict[str, Any]]:
        """Return all tracked domains with their 24h summary metrics using exact distinct visitors."""
        return fetch_domains_summary(self.resolve_domain_log_path)

    def get_domain_detail(self, domain_name: str, days: int = 7) -> Dict[str, Any]:
        """Fetch detailed charts and reports for a specific domain with exact visitor counts."""
        return fetch_domain_detail(domain_name, days, self.resolve_domain_log_path)

    def process_all_active_domains(self) -> int:
        """Parse new logs for actively tracked domains only."""
        with get_db() as conn:
            domains = conn.execute("SELECT * FROM tracked_domains WHERE is_active = 1").fetchall()

        total_processed = 0
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        for d in domains:
            domain_name = d["domain_name"]
            saved_path = d["log_path"]
            log_path = Path(saved_path) if saved_path and Path(saved_path).exists() else Path(self.resolve_domain_log_path(domain_name))
            if not log_path.exists():
                continue

            entries, new_offset, new_inode = parse_log_file(log_path, last_offset=d["last_offset"], last_inode=d["last_inode"])
            if entries:
                process_domain_entries(domain_name, entries)
                total_processed += len(entries)

            with get_db() as conn:
                conn.execute("""
                    UPDATE tracked_domains 
                    SET last_offset = ?, last_inode = ?, last_parsed_at = ?
                    WHERE domain_name = ?
                """, (new_offset, new_inode, now_str, domain_name))

        return total_processed

    async def run_worker_loop(self, interval_seconds: int = 30) -> None:
        """Continuous background worker parsing active domain logs."""
        self._running = True
        logger.info("Domain Analytics background worker started (interval: %ss)", interval_seconds)
        try:
            while self._running:
                try:
                    await asyncio.to_thread(self.process_all_active_domains)
                    now_dt = datetime.now(timezone.utc)
                    if now_dt.hour == 3 and now_dt.minute < 2:
                        await asyncio.to_thread(prune_old_data, 60)
                except Exception as exc:
                    logger.error("Domain Analytics worker cycle error: %s", exc)
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Domain Analytics worker stopped.")
        finally:
            self._running = False


domain_analytics_service = DomainAnalyticsService()
