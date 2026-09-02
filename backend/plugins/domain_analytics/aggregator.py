"""
plugins/domain_analytics/aggregator.py — Aggregate log entries into SQLite summary tables.
Handles hourly rollups, top paths/referrers, errors, optional GeoIP, and data retention.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import List

from plugins.domain_analytics.models import LogEntry
from plugins.domain_analytics.db import get_db
from plugins.domain_analytics.geoip_service import geoip_service

logger = logging.getLogger(__name__)


def process_domain_entries(domain_name: str, entries: List[LogEntry]) -> None:
    """Aggregate a batch of LogEntry items into SQLite tables."""
    if not entries:
        return

    geoip_active = geoip_service.is_enabled()

    # Buckets for in-memory grouping before writing to DB
    hourly_buckets = defaultdict(lambda: {
        "requests": 0, "ips": set(), "bytes": 0,
        "2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0,
        "total_time": 0.0, "max_time": 0.0
    })
    path_buckets = defaultdict(lambda: {"hits": 0, "bytes": 0, "total_time": 0.0})
    ref_buckets = defaultdict(int)
    geo_buckets = defaultdict(int)
    errors = []

    for entry in entries:
        # 1. Hourly bucket
        hour_ts = entry.timestamp[:13] + ":00:00"  # YYYY-MM-DD HH:00:00
        hb = hourly_buckets[hour_ts]
        hb["requests"] += 1
        hb["ips"].add(entry.ip)
        hb["bytes"] += entry.bytes_sent
        hb["total_time"] += entry.request_time
        if entry.request_time > hb["max_time"]:
            hb["max_time"] = entry.request_time

        if 200 <= entry.status < 300:
            hb["2xx"] += 1
        elif 300 <= entry.status < 400:
            hb["3xx"] += 1
        elif 400 <= entry.status < 500:
            hb["4xx"] += 1
        elif 500 <= entry.status < 600:
            hb["5xx"] += 1

        # 2. Daily top paths
        day_date = entry.timestamp[:10]  # YYYY-MM-DD
        pb = path_buckets[(day_date, entry.path)]
        pb["hits"] += 1
        pb["bytes"] += entry.bytes_sent
        pb["total_time"] += entry.request_time

        # 3. Daily top referrers
        if entry.referrer:
            ref_buckets[(day_date, entry.referrer)] += 1

        # 4. Error logs (4xx and 5xx)
        if entry.status >= 400:
            errors.append((entry.timestamp, entry.status, entry.path, entry.ip, entry.referrer))

        # 5. GeoIP stats (if enabled)
        if geoip_active:
            loc = geoip_service.lookup(entry.ip)
            if loc:
                geo_buckets[(day_date, loc.country_code, loc.country_name, loc.city_name or "")] += 1

    # Batch write to SQLite
    with get_db() as conn:
        cursor = conn.cursor()

        # Update hourly_stats
        for hour_ts, data in hourly_buckets.items():
            avg_time = (data["total_time"] / data["requests"]) * 1000.0 if data["requests"] else 0.0
            cursor.execute("""
                INSERT INTO hourly_stats (
                    domain_name, hour_timestamp, total_requests, unique_ips, bandwidth_bytes,
                    status_2xx, status_3xx, status_4xx, status_5xx, avg_response_time_ms, max_response_time_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain_name, hour_timestamp) DO UPDATE SET
                    total_requests = total_requests + excluded.total_requests,
                    unique_ips = unique_ips + excluded.unique_ips,
                    bandwidth_bytes = bandwidth_bytes + excluded.bandwidth_bytes,
                    status_2xx = status_2xx + excluded.status_2xx,
                    status_3xx = status_3xx + excluded.status_3xx,
                    status_4xx = status_4xx + excluded.status_4xx,
                    status_5xx = status_5xx + excluded.status_5xx,
                    avg_response_time_ms = (avg_response_time_ms + excluded.avg_response_time_ms) / 2.0,
                    max_response_time_ms = MAX(max_response_time_ms, excluded.max_response_time_ms);
            """, (
                domain_name, hour_ts, data["requests"], len(data["ips"]), data["bytes"],
                data["2xx"], data["3xx"], data["4xx"], data["5xx"], avg_time, data["max_time"] * 1000.0
            ))

        # Update top_paths
        for (day_date, path), data in path_buckets.items():
            avg_time = (data["total_time"] / data["hits"]) * 1000.0 if data["hits"] else 0.0
            cursor.execute("""
                INSERT INTO top_paths (domain_name, day_date, path, hits, bandwidth_bytes, avg_time_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain_name, day_date, path) DO UPDATE SET
                    hits = hits + excluded.hits,
                    bandwidth_bytes = bandwidth_bytes + excluded.bandwidth_bytes,
                    avg_time_ms = (avg_time_ms + excluded.avg_time_ms) / 2.0;
            """, (domain_name, day_date, path, data["hits"], data["bytes"], avg_time))

        # Update top_referrers
        for (day_date, ref), hits in ref_buckets.items():
            cursor.execute("""
                INSERT INTO top_referrers (domain_name, day_date, referrer, hits)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(domain_name, day_date, referrer) DO UPDATE SET
                    hits = hits + excluded.hits;
            """, (domain_name, day_date, ref, hits))

        # Insert errors (limit to recent 100 per batch)
        for ts, status, path, ip, ref in errors[:100]:
            cursor.execute("""
                INSERT INTO error_logs (domain_name, timestamp, status_code, path, ip, referrer, count)
                VALUES (?, ?, ?, ?, ?, ?, 1);
            """, (domain_name, ts, status, path, ip, ref))

        # Update geo_stats
        for (day_date, c_code, c_name, city), hits in geo_buckets.items():
            cursor.execute("""
                INSERT INTO geo_stats (domain_name, day_date, country_code, country_name, city_name, hits)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain_name, day_date, country_code, city_name) DO UPDATE SET
                    hits = hits + excluded.hits;
            """, (domain_name, day_date, c_code, c_name, city or None, hits))


def prune_old_data(retention_days: int = 60) -> int:
    """Purge statistics older than retention_days to maintain minimal DB size."""
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    cutoff_ts = f"{cutoff_date} 00:00:00"

    with get_db() as conn:
        c1 = conn.execute("DELETE FROM hourly_stats WHERE hour_timestamp < ?", (cutoff_ts,)).rowcount
        c2 = conn.execute("DELETE FROM top_paths WHERE day_date < ?", (cutoff_date,)).rowcount
        c3 = conn.execute("DELETE FROM top_referrers WHERE day_date < ?", (cutoff_date,)).rowcount
        c4 = conn.execute("DELETE FROM error_logs WHERE timestamp < ?", (cutoff_ts,)).rowcount
        c5 = conn.execute("DELETE FROM geo_stats WHERE day_date < ?", (cutoff_date,)).rowcount
        total = c1 + c2 + c3 + c4 + c5
        if total > 0:
            logger.info("Pruned %d expired domain analytics rows (older than %d days)", total, retention_days)
        return total
