"""
plugins/domain_analytics/service.py — Domain Analytics service lifecycle & background worker.
"""
from __future__ import annotations

import os
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

logger = logging.getLogger(__name__)


class DomainAnalyticsService:
    plugin_id = "domain_analytics"

    def __init__(self):
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        init_db()

    def is_installed(self) -> bool:
        return True

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
        """Find the appropriate Nginx access log path for the domain across standard and custom /opt paths."""
        log_dirs = [
            Path(getattr(config, "NGINX_LOG_DIR", "/var/log/nginx")),
            Path("/var/log/nginx"),
            Path("/opt/nginx/logs"),
            Path("/opt/openresty/nginx/logs"),
            Path("/usr/local/nginx/logs"),
        ]

        candidates = []
        for ldir in log_dirs:
            candidates.append(ldir / "domains" / f"{domain_name}.access.log")
            candidates.append(ldir / f"{domain_name}.access.log")
            candidates.append(ldir / f"{domain_name}-access.log")

        candidates.append(Path(config.NGINX_WEBROOT) / domain_name / "logs" / "access.log")
        candidates.append(Path(config.NGINX_WEBROOT) / domain_name / "access.log")

        php_root = Path(config.PHP_SITE_LOG_ROOT)
        if php_root.exists():
            for p in php_root.iterdir():
                acc = p / "access.log"
                if acc.exists():
                    candidates.append(acc)

        for c in candidates:
            if c.exists() and c.is_file():
                return str(c)

        for ldir in log_dirs:
            fallback = ldir / "access.log"
            if fallback.exists():
                return str(fallback)

        return str(candidates[0])

    def process_domain_log(self, domain_name: str) -> dict:
        """Process logs for a specific domain immediately and return diagnostics."""
        log_path_str = self.resolve_domain_log_path(domain_name)
        log_path = Path(log_path_str)

        with get_db() as conn:
            d = conn.execute("SELECT * FROM tracked_domains WHERE domain_name = ?", (domain_name,)).fetchone()
            last_offset = d["last_offset"] if d else 0
            last_inode = d["last_inode"] if d else 0

        if not log_path.exists() or not log_path.is_file():
            return {
                "success": False,
                "log_path": str(log_path),
                "file_exists": False,
                "processed_lines": 0,
                "message": f"Log file not found at {log_path}",
            }

        entries, new_offset, new_inode = parse_log_file(
            log_path,
            last_offset=last_offset,
            last_inode=last_inode,
        )

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
        """Synchronize known domains into tracked_domains table."""
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
        logger.info("Domain %s analytics tracking set to %s", domain_name, is_active)
        return True

    def get_domain_active_status(self, domain_name: str) -> bool:
        with get_db() as conn:
            row = conn.execute("SELECT is_active FROM tracked_domains WHERE domain_name = ?", (domain_name,)).fetchone()
            return bool(row and row["is_active"] == 1)

    def list_domains_summary(self) -> List[Dict[str, Any]]:
        """Return all tracked domains with their 24h summary metrics."""
        now = datetime.now(timezone.utc)
        day_ago = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

        with get_db() as conn:
            domains = conn.execute("SELECT domain_name, is_active, last_parsed_at FROM tracked_domains ORDER BY is_active DESC, domain_name ASC").fetchall()
            results = []
            for d in domains:
                dname = d["domain_name"]
                stats = conn.execute("""
                    SELECT 
                        SUM(total_requests) as requests,
                        SUM(unique_ips) as ips,
                        SUM(bandwidth_bytes) as bytes,
                        SUM(status_4xx + status_5xx) as errors,
                        AVG(avg_response_time_ms) as avg_time
                    FROM hourly_stats
                    WHERE domain_name = ? AND hour_timestamp >= ?
                """, (dname, day_ago)).fetchone()

                reqs = stats["requests"] or 0
                errs = stats["errors"] or 0
                error_rate = round((errs / reqs) * 100, 1) if reqs > 0 else 0.0

                results.append({
                    "domain_name": dname,
                    "is_active": bool(d["is_active"]),
                    "last_parsed_at": d["last_parsed_at"],
                    "requests_24h": reqs,
                    "unique_ips_24h": stats["ips"] or 0,
                    "bandwidth_bytes_24h": stats["bytes"] or 0,
                    "error_rate_24h": error_rate,
                    "avg_response_time_ms": round(stats["avg_time"] or 0.0, 1),
                })
            return results

    def get_domain_detail(self, domain_name: str, days: int = 7) -> Dict[str, Any]:
        """Fetch detailed charts and reports for a specific domain."""
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
        cutoff_date = cutoff_dt.strftime("%Y-%m-%d")

        with get_db() as conn:
            domain_info = conn.execute(
                "SELECT * FROM tracked_domains WHERE domain_name = ?", (domain_name,)
            ).fetchone()
            if not domain_info:
                # Ensure domain entry exists
                self.toggle_domain(domain_name, is_active=False)
                domain_info = {"domain_name": domain_name, "is_active": 0}

            # Hourly timeline
            hourly_rows = conn.execute("""
                SELECT hour_timestamp, total_requests, unique_ips, bandwidth_bytes,
                       status_2xx, status_3xx, status_4xx, status_5xx, avg_response_time_ms
                FROM hourly_stats
                WHERE domain_name = ? AND hour_timestamp >= ?
                ORDER BY hour_timestamp ASC
            """, (domain_name, cutoff_str)).fetchall()

            # Top paths
            paths = conn.execute("""
                SELECT path, SUM(hits) as total_hits, SUM(bandwidth_bytes) as total_bytes, AVG(avg_time_ms) as avg_time
                FROM top_paths
                WHERE domain_name = ? AND day_date >= ?
                GROUP BY path
                ORDER BY total_hits DESC LIMIT 15
            """, (domain_name, cutoff_date)).fetchall()

            # Top referrers
            referrers = conn.execute("""
                SELECT referrer, SUM(hits) as total_hits
                FROM top_referrers
                WHERE domain_name = ? AND day_date >= ?
                GROUP BY referrer
                ORDER BY total_hits DESC LIMIT 10
            """, (domain_name, cutoff_date)).fetchall()

            # Recent errors
            errors = conn.execute("""
                SELECT timestamp, status_code, path, ip, referrer
                FROM error_logs
                WHERE domain_name = ? AND timestamp >= ?
                ORDER BY timestamp DESC LIMIT 20
            """, (domain_name, cutoff_str)).fetchall()

            # GeoIP breakdown (if any)
            geo_rows = conn.execute("""
                SELECT country_code, country_name, city_name, SUM(hits) as total_hits
                FROM geo_stats
                WHERE domain_name = ? AND day_date >= ?
                GROUP BY country_code, city_name
                ORDER BY total_hits DESC LIMIT 15
            """, (domain_name, cutoff_date)).fetchall()

            # Total aggregates
            totals = conn.execute("""
                SELECT 
                    SUM(total_requests) as requests,
                    SUM(unique_ips) as ips,
                    SUM(bandwidth_bytes) as bytes,
                    SUM(status_2xx) as s2xx,
                    SUM(status_3xx) as s3xx,
                    SUM(status_4xx) as s4xx,
                    SUM(status_5xx) as s5xx,
                    AVG(avg_response_time_ms) as avg_time
                FROM hourly_stats
                WHERE domain_name = ? AND hour_timestamp >= ?
            """, (domain_name, cutoff_str)).fetchone()

            return {
                "domain_name": domain_name,
                "is_active": bool(domain_info["is_active"]),
                "days": days,
                "totals": {
                    "requests": totals["requests"] or 0,
                    "unique_ips": totals["ips"] or 0,
                    "bandwidth_bytes": totals["bytes"] or 0,
                    "status_2xx": totals["s2xx"] or 0,
                    "status_3xx": totals["s3xx"] or 0,
                    "status_4xx": totals["s4xx"] or 0,
                    "status_5xx": totals["s5xx"] or 0,
                    "avg_response_time_ms": round(totals["avg_time"] or 0.0, 1),
                },
                "timeline": [dict(r) for r in hourly_rows],
                "top_paths": [dict(r) for r in paths],
                "top_referrers": [dict(r) for r in referrers],
                "recent_errors": [dict(r) for r in errors],
                "geo_stats": [dict(r) for r in geo_rows],
                "geoip_enabled": geoip_service.is_enabled(),
            }

    def process_all_active_domains(self) -> int:
        """Parse new logs for all actively tracked domains."""
        with get_db() as conn:
            domains = conn.execute("SELECT * FROM tracked_domains WHERE is_active = 1").fetchall()

        total_processed = 0
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        for d in domains:
            domain_name = d["domain_name"]
            log_path = Path(d["log_path"] or self.resolve_domain_log_path(domain_name))
            if not log_path.exists():
                continue

            entries, new_offset, new_inode = parse_log_file(
                log_path,
                last_offset=d["last_offset"],
                last_inode=d["last_inode"],
            )

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
                    # Periodic retention prune once every 24h
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
