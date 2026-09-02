"""
plugins/domain_analytics/geoip_service.py — Hybrid GeoIP resolver with MMDB Download Manager.
Supports MaxMind binary databases (.mmdb) via pure-Python reader or C-extension, plus SQLite caching.
"""
from __future__ import annotations

import json
import shutil
import logging
import urllib.request
from pathlib import Path
from typing import Optional

from plugins.domain_analytics.models import GeoLocation
from plugins.domain_analytics.db import DATA_DIR, get_db

logger = logging.getLogger(__name__)

MMDB_URLS = {
    "country": "https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-Country.mmdb",
    "city": "https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-City.mmdb",
}
DEFAULT_MMDB_PATH = DATA_DIR / "GeoLite2.mmdb"


class GeoIPService:
    def __init__(self):
        self._cache_memory: dict[str, Optional[GeoLocation]] = {}
        self._db_path: Path = DEFAULT_MMDB_PATH
        self._reader = None
        self._load_reader()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def is_installed(self) -> bool:
        return self._db_path.exists() and self._db_path.stat().st_size > 1024

    def _load_reader(self) -> None:
        """Attempt to open .mmdb via C-extension or built-in pure Python reader."""
        if not self.is_installed():
            self._reader = None
            return
        try:
            import maxminddb
            self._reader = maxminddb.open_database(str(self._db_path))
            logger.info("Loaded GeoIP .mmdb with maxminddb C-engine: %s", self._db_path)
        except ImportError:
            try:
                from plugins.domain_analytics.mmdb_reader import PureMMDBReader
                self._reader = PureMMDBReader(self._db_path)
                logger.info("Loaded GeoIP .mmdb with PureMMDBReader (zero-dependency): %s", self._db_path)
            except Exception as exc:
                logger.warning("Failed to open %s with PureMMDBReader: %s", self._db_path, exc)
                self._reader = None

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

    def download_database(self, db_type: str = "country", custom_url: str = "") -> tuple[bool, str]:
        """Download GeoLite2 database from GitHub or custom URL."""
        url = custom_url.strip() if custom_url.strip() else MMDB_URLS.get(db_type, MMDB_URLS["country"])
        temp_path = DATA_DIR / "GeoLite2.mmdb.download"

        try:
            logger.info("Downloading GeoIP database from %s...", url)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (SRV Panel GeoIP Sync)"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(temp_path, "wb") as f:
                shutil.copyfileobj(resp, f)

            if temp_path.stat().st_size < 1024:
                temp_path.unlink(missing_ok=True)
                return False, "Downloaded file is too small or invalid."

            if self._reader and hasattr(self._reader, "close"):
                try:
                    self._reader.close()
                except Exception:
                    pass

            temp_path.replace(self._db_path)
            self._load_reader()
            self.set_enabled(True)

            with get_db() as conn:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES ('geoip_db_type', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (db_type,),
                )
            return True, "GeoIP database downloaded and activated successfully."
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            logger.error("GeoIP download failed: %s", exc)
            return False, f"Download failed: {exc}"

    def install_custom_file(self, source_path: Path) -> tuple[bool, str]:
        """Install an uploaded .mmdb database file."""
        if not source_path.exists() or source_path.stat().st_size < 1024:
            return False, "Uploaded file is invalid or empty."
        try:
            if self._reader and hasattr(self._reader, "close"):
                try:
                    self._reader.close()
                except Exception:
                    pass
            shutil.copyfile(str(source_path), str(self._db_path))
            self._load_reader()
            self.set_enabled(True)
            return True, "Custom GeoIP database installed successfully."
        except Exception as exc:
            return False, f"Failed to install database: {exc}"

    def get_settings(self) -> dict:
        with get_db() as conn:
            enabled = self.is_enabled()
            cached_count = conn.execute("SELECT COUNT(*) as c FROM ip_cache").fetchone()["c"]
            row_type = conn.execute("SELECT value FROM settings WHERE key = 'geoip_db_type'").fetchone()
            db_type = row_type["value"] if row_type else "country"
        return {
            "enabled": enabled,
            "installed": self.is_installed(),
            "has_reader": self._reader is not None,
            "path": str(self._db_path),
            "size_mb": round(self._db_path.stat().st_size / (1024 * 1024), 2) if self.is_installed() else 0,
            "db_type": db_type,
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

        if self._is_private_ip(ip):
            return GeoLocation(country_code="LOCAL", country_name="Local Network / LAN", city_name="Internal")

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

        loc = None

        # 3. If MMDB reader is loaded, query from local .mmdb file
        if self._reader:
            try:
                record = self._reader.get(ip)
                if record and isinstance(record, dict):
                    c_data = record.get("country", {}) or record.get("registered_country", {})
                    c_code = c_data.get("iso_code", "XX").upper()
                    c_name = c_data.get("names", {}).get("en", "Unknown")
                    city_data = record.get("city", {})
                    city_name = city_data.get("names", {}).get("en")
                    loc = GeoLocation(country_code=c_code, country_name=c_name, city_name=city_name)
            except Exception as exc:
                logger.debug("MMDB reader lookup failed for %s: %s", ip, exc)

        # 4. Fallback to lightweight web JSON lookup if MMDB file not present
        if not loc:
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
            except Exception as exc:
                logger.debug("Web GeoIP fallback failed for %s: %s", ip, exc)

        if not loc:
            loc = GeoLocation(country_code="XX", country_name="Unknown", city_name=None)

        # Persist to SQLite cache so it is never resolved again
        try:
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
        except Exception:
            pass

        self._cache_memory[ip] = loc
        return loc


geoip_service = GeoIPService()
