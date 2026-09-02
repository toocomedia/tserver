"""
plugins/domain_analytics/geoip_service.py — Optional GeoIP lookup and database manager.
Supports MaxMind GeoLite2 Country and City .mmdb formats.
"""
from __future__ import annotations

import os
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
        self._reader = None
        self._db_path: Path = DEFAULT_MMDB_PATH
        self._initialized = False

    @property
    def db_path(self) -> Path:
        return self._db_path

    def is_installed(self) -> bool:
        return self._db_path.exists() and self._db_path.stat().st_size > 1024

    def is_enabled(self) -> bool:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'geoip_enabled'").fetchone()
            if row is not None:
                return row["value"] == "1"
            return self.is_installed()

    def set_enabled(self, enabled: bool) -> None:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('geoip_enabled', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if enabled else "0",),
            )
        if enabled:
            self._load_reader()
        else:
            self._close_reader()

    def get_settings(self) -> dict:
        with get_db() as conn:
            rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'geoip_%'").fetchall()
            data = {r["key"]: r["value"] for r in rows}
        return {
            "enabled": data.get("geoip_enabled") == "1",
            "db_type": data.get("geoip_db_type", "country"),
            "custom_url": data.get("geoip_custom_url", ""),
            "installed": self.is_installed(),
            "reader_loaded": self._reader is not None,
            "path": str(self._db_path),
            "size_mb": round(self._db_path.stat().st_size / (1024 * 1024), 2) if self.is_installed() else 0,
        }

    def _load_reader(self) -> None:
        if not self.is_installed():
            self._reader = None
            return
        try:
            import maxminddb
            self._reader = maxminddb.open_database(str(self._db_path))
            logger.info("Loaded GeoIP database: %s", self._db_path)
        except ImportError:
            logger.warning("maxminddb library not installed. GeoIP lookups will be skipped.")
            self._reader = None
        except Exception as exc:
            logger.error("Failed to open GeoIP database %s: %s", self._db_path, exc)
            self._reader = None

    def _close_reader(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
            self._reader = None

    def download_database(self, db_type: str = "country", custom_url: str = "") -> tuple[bool, str]:
        """Download GeoLite2 database from github or custom URL."""
        url = custom_url.strip() if custom_url.strip() else MMDB_URLS.get(db_type, MMDB_URLS["country"])
        target_path = self._db_path
        temp_path = DATA_DIR / "GeoLite2.mmdb.download"

        try:
            logger.info("Downloading GeoIP database from %s...", url)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (SRV Panel GeoIP Sync)"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(temp_path, "wb") as f:
                shutil.copyfileobj(resp, f)

            if temp_path.stat().st_size < 1024:
                temp_path.unlink(missing_ok=True)
                return False, "Downloaded file is too small or invalid."

            self._close_reader()
            temp_path.replace(target_path)
            self._load_reader()

            # Save settings and enable
            self.set_enabled(True)
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES ('geoip_db_type', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (db_type,),
                )
                if custom_url:
                    conn.execute(
                        "INSERT INTO settings (key, value) VALUES ('geoip_custom_url', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (custom_url,),
                    )

            return True, "GeoIP database downloaded and activated successfully."
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            logger.error("GeoIP download failed: %s", exc)
            return False, f"Download failed: {exc}"

    def install_custom_file(self, source_path: Path) -> tuple[bool, str]:
        """Install an uploaded .mmdb file."""
        if not source_path.exists() or source_path.stat().st_size < 1024:
            return False, "Uploaded file is invalid or empty."
        try:
            self._close_reader()
            shutil.copyfile(str(source_path), str(self._db_path))
            self._load_reader()
            return True, "Custom GeoIP database installed successfully."
        except Exception as exc:
            return False, f"Failed to install custom GeoIP database: {exc}"

    def lookup(self, ip: str) -> Optional[GeoLocation]:
        """Lookup country code, country name, and city name for an IP address."""
        if not self._reader or not ip:
            return None

        # Handle local / private LAN IPs gracefully for local testing
        if ip in ("127.0.0.1", "::1", "localhost") or ip.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.3")):
            return GeoLocation(
                country_code="LOCAL",
                country_name="Local Network / LAN",
                city_name="Internal",
            )

        try:
            record = self._reader.get(ip)
            if not record or not isinstance(record, dict):
                return None

            country_data = record.get("country", {}) or record.get("registered_country", {})
            country_code = country_data.get("iso_code", "XX")
            country_name = country_data.get("names", {}).get("en", "Unknown")

            city_data = record.get("city", {})
            city_name = city_data.get("names", {}).get("en")

            return GeoLocation(
                country_code=country_code.upper(),
                country_name=country_name,
                city_name=city_name,
            )
        except Exception:
            return None


geoip_service = GeoIPService()
