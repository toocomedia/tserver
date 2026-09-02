"""
plugins/domain_analytics/geoip_service.py — Lightweight, zero-dependency optional GeoIP resolver.
Uses SQLite persistent caching so each distinct visitor IP is resolved at most once in its lifetime.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Optional

from plugins.domain_analytics.models import GeoLocation
from plugins.domain_analytics.db import get_db

logger = logging.getLogger(__name__)


class GeoIPService:
    def __init__(self):
        self._cache_memory: dict[str, Optional[GeoLocation]] = {}

    def is_enabled(self) -> bool:
        """Check if GeoIP country tracking is enabled in settings."""
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'geoip_enabled'").fetchone()
            return bool(row and row["value"] == "1")

    def set_enabled(self, enabled: bool) -> None:
        """Toggle GeoIP country tracking on or off."""
        with get_db() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('geoip_enabled', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if enabled else "0",),
            )
        logger.info("GeoIP country tracking set to %s", enabled)

    def get_settings(self) -> dict:
        with get_db() as conn:
            enabled = self.is_enabled()
            cached_count = conn.execute("SELECT COUNT(*) as c FROM ip_cache").fetchone()["c"]
        return {
            "enabled": enabled,
            "cached_ips": cached_count,
        }

    def _is_private_ip(self, ip: str) -> bool:
        return (
            ip in ("127.0.0.1", "::1", "localhost")
            or ip.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.3", "fc00:", "fe80:"))
        )

    def lookup(self, ip: str) -> Optional[GeoLocation]:
        """Lookup country code, country name, and city name for an IP address."""
        if not ip or not self.is_enabled():
            return None

        # Handle local / private LAN IPs gracefully
        if self._is_private_ip(ip):
            return GeoLocation(
                country_code="LOCAL",
                country_name="Local Network / LAN",
                city_name="Internal",
            )

        # 1. Fast in-memory process cache
        if ip in self._cache_memory:
            return self._cache_memory[ip]

        # 2. Check persistent SQLite cache
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT country_code, country_name, city_name FROM ip_cache WHERE ip = ?", (ip,)
                ).fetchone()
                if row:
                    loc = GeoLocation(
                        country_code=row["country_code"],
                        country_name=row["country_name"],
                        city_name=row["city_name"] or None,
                    )
                    self._cache_memory[ip] = loc
                    return loc
        except Exception as exc:
            logger.debug("Error checking ip_cache: %s", exc)

        # 3. Resolve unseen IP via lightweight built-in HTTP request
        try:
            url = f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,city"
            req = urllib.request.Request(url, headers={"User-Agent": "SRV-Panel-Analytics/1.0"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("status") == "success":
                loc = GeoLocation(
                    country_code=data.get("countryCode", "XX").upper(),
                    country_name=data.get("country", "Unknown"),
                    city_name=data.get("city") or None,
                )
            else:
                loc = GeoLocation(country_code="XX", country_name="Unknown", city_name=None)

            # Persist to SQLite cache so it is never resolved again
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO ip_cache (ip, country_code, country_name, city_name)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(ip) DO UPDATE SET
                        country_code = excluded.country_code,
                        country_name = excluded.country_name,
                        city_name = excluded.city_name,
                        updated_at = CURRENT_TIMESTAMP;
                """, (ip, loc.country_code, loc.country_name, loc.city_name))

            self._cache_memory[ip] = loc
            return loc
        except Exception as exc:
            logger.debug("GeoIP lookup failed for %s: %s", ip, exc)
            return None


geoip_service = GeoIPService()
