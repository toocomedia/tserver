"""
plugins/domain_analytics/queries.py — Database queries for summary and detailed analytics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List

from plugins.domain_analytics.db import get_db
from plugins.domain_analytics.geoip_service import geoip_service


def fetch_domains_summary(resolve_log_fn) -> List[Dict[str, Any]]:
    """Return all tracked domains with their 24h summary metrics using exact distinct visitors."""
    now = datetime.now(timezone.utc)
    day_ago_ts = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    day_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    with get_db() as conn:
        domains = conn.execute(
            "SELECT domain_name, is_active, last_parsed_at, log_path "
            "FROM tracked_domains ORDER BY is_active DESC, domain_name ASC"
        ).fetchall()
        results = []
        for d in domains:
            dname = d["domain_name"]
            saved_path = d["log_path"]
            log_path = Path(saved_path) if saved_path and Path(saved_path).exists() else Path(resolve_log_fn(dname))

            stats = conn.execute("""
                SELECT 
                    SUM(total_requests) as requests,
                    SUM(bandwidth_bytes) as bytes,
                    SUM(status_4xx + status_5xx) as errors,
                    AVG(avg_response_time_ms) as avg_time
                FROM hourly_stats
                WHERE domain_name = ? AND hour_timestamp >= ?
            """, (dname, day_ago_ts)).fetchone()

            distinct_visitors = conn.execute("""
                SELECT COUNT(DISTINCT ip) as ips FROM daily_visitors
                WHERE domain_name = ? AND day_date >= ?
            """, (dname, day_date)).fetchone()["ips"] or 0

            reqs = stats["requests"] or 0
            errs = stats["errors"] or 0
            error_rate = round((errs / reqs) * 100, 1) if reqs > 0 else 0.0

            results.append({
                "domain_name": dname,
                "is_active": bool(d["is_active"]),
                "last_parsed_at": d["last_parsed_at"],
                "requests_24h": reqs,
                "unique_ips_24h": distinct_visitors,
                "bandwidth_bytes_24h": stats["bytes"] or 0,
                "error_rate_24h": error_rate,
                "avg_response_time_ms": round(stats["avg_time"] or 0.0, 1),
                "diagnostic_log_path": str(log_path),
                "diagnostic_log_exists": log_path.exists(),
            })
        return results


def fetch_domain_detail(domain_name: str, days: int, resolve_log_fn) -> Dict[str, Any]:
    """Fetch detailed charts and reports for a specific domain with exact visitor counts."""
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
    cutoff_date = cutoff_dt.strftime("%Y-%m-%d")

    with get_db() as conn:
        domain_info = conn.execute("SELECT * FROM tracked_domains WHERE domain_name = ?", (domain_name,)).fetchone()
        is_active = bool(domain_info["is_active"]) if domain_info else False
        saved_path = domain_info["log_path"] if domain_info else ""

        log_path = Path(saved_path) if saved_path and Path(saved_path).exists() else Path(resolve_log_fn(domain_name))
        log_exists = log_path.exists()
        log_size = log_path.stat().st_size if log_exists else 0

        hourly_rows = conn.execute("""
            SELECT hour_timestamp, total_requests, unique_ips, bandwidth_bytes,
                   status_2xx, status_3xx, status_4xx, status_5xx, avg_response_time_ms
            FROM hourly_stats
            WHERE domain_name = ? AND hour_timestamp >= ?
            ORDER BY hour_timestamp ASC
        """, (domain_name, cutoff_str)).fetchall()

        paths = conn.execute("""
            SELECT path, SUM(hits) as total_hits, SUM(bandwidth_bytes) as total_bytes, AVG(avg_time_ms) as avg_time
            FROM top_paths
            WHERE domain_name = ? AND day_date >= ?
            GROUP BY path
            ORDER BY total_hits DESC LIMIT 15
        """, (domain_name, cutoff_date)).fetchall()

        referrers = conn.execute("""
            SELECT referrer, SUM(hits) as total_hits
            FROM top_referrers
            WHERE domain_name = ? AND day_date >= ?
            GROUP BY referrer
            ORDER BY total_hits DESC LIMIT 10
        """, (domain_name, cutoff_date)).fetchall()

        errors = conn.execute("""
            SELECT timestamp, status_code, path, ip, referrer
            FROM error_logs
            WHERE domain_name = ? AND timestamp >= ?
            ORDER BY timestamp DESC LIMIT 20
        """, (domain_name, cutoff_str)).fetchall()

        geo_rows = conn.execute("""
            SELECT country_code, country_name, city_name, SUM(hits) as total_hits
            FROM geo_stats
            WHERE domain_name = ? AND day_date >= ?
            GROUP BY country_code, city_name
            ORDER BY total_hits DESC LIMIT 15
        """, (domain_name, cutoff_date)).fetchall()

        totals = conn.execute("""
            SELECT 
                SUM(total_requests) as requests,
                SUM(bandwidth_bytes) as bytes,
                SUM(status_2xx) as s2xx,
                SUM(status_3xx) as s3xx,
                SUM(status_4xx) as s4xx,
                SUM(status_5xx) as s5xx,
                AVG(avg_response_time_ms) as avg_time
            FROM hourly_stats
            WHERE domain_name = ? AND hour_timestamp >= ?
        """, (domain_name, cutoff_str)).fetchone()

        distinct_visitors = conn.execute("""
            SELECT COUNT(DISTINCT ip) as ips FROM daily_visitors
            WHERE domain_name = ? AND day_date >= ?
        """, (domain_name, cutoff_date)).fetchone()["ips"] or 0

        return {
            "domain_name": domain_name,
            "is_active": is_active,
            "days": days,
            "diagnostics": {
                "log_path": str(log_path),
                "file_exists": log_exists,
                "file_size_bytes": log_size,
            },
            "totals": {
                "requests": totals["requests"] or 0,
                "unique_ips": distinct_visitors,
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
