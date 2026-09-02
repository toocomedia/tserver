"""
plugins/domain_analytics/db.py — Dedicated SQLite storage for analytics.
Keeps analytics time-series data completely isolated from panel.db.
"""
from __future__ import annotations

import os
import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("ANALYTICS_DATA_DIR", "/var/lib/srv-panel/plugins/domain_analytics"))
if not DATA_DIR.exists():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Fallback to local plugin data directory (e.g. dev/Windows)
        DATA_DIR = Path(__file__).parent / "data"
        DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "analytics.db"


def get_db_path() -> Path:
    return DB_PATH


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding a SQLite connection configured with WAL mode."""
    conn = sqlite3.connect(str(DB_PATH), timeout=20.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 10000;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize dedicated analytics tables and performance indices."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.executescript("""
        CREATE TABLE IF NOT EXISTS tracked_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_name TEXT UNIQUE NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            log_path TEXT,
            last_offset INTEGER NOT NULL DEFAULT 0,
            last_inode INTEGER NOT NULL DEFAULT 0,
            last_parsed_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS hourly_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_name TEXT NOT NULL,
            hour_timestamp TEXT NOT NULL, -- YYYY-MM-DD HH:00:00
            total_requests INTEGER NOT NULL DEFAULT 0,
            unique_ips INTEGER NOT NULL DEFAULT 0,
            bandwidth_bytes INTEGER NOT NULL DEFAULT 0,
            status_2xx INTEGER NOT NULL DEFAULT 0,
            status_3xx INTEGER NOT NULL DEFAULT 0,
            status_4xx INTEGER NOT NULL DEFAULT 0,
            status_5xx INTEGER NOT NULL DEFAULT 0,
            avg_response_time_ms REAL NOT NULL DEFAULT 0.0,
            max_response_time_ms REAL NOT NULL DEFAULT 0.0,
            UNIQUE(domain_name, hour_timestamp)
        );

        CREATE TABLE IF NOT EXISTS top_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_name TEXT NOT NULL,
            day_date TEXT NOT NULL, -- YYYY-MM-DD
            path TEXT NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0,
            bandwidth_bytes INTEGER NOT NULL DEFAULT 0,
            avg_time_ms REAL NOT NULL DEFAULT 0.0,
            UNIQUE(domain_name, day_date, path)
        );

        CREATE TABLE IF NOT EXISTS top_referrers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_name TEXT NOT NULL,
            day_date TEXT NOT NULL, -- YYYY-MM-DD
            referrer TEXT NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0,
            UNIQUE(domain_name, day_date, referrer)
        );

        CREATE TABLE IF NOT EXISTS error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_name TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            path TEXT NOT NULL,
            ip TEXT NOT NULL,
            referrer TEXT,
            count INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS geo_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_name TEXT NOT NULL,
            day_date TEXT NOT NULL, -- YYYY-MM-DD
            country_code TEXT NOT NULL,
            country_name TEXT NOT NULL,
            city_name TEXT,
            hits INTEGER NOT NULL DEFAULT 0,
            UNIQUE(domain_name, day_date, country_code, city_name)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- Performance Indices
        CREATE INDEX IF NOT EXISTS idx_hourly_domain_time ON hourly_stats(domain_name, hour_timestamp);
        CREATE INDEX IF NOT EXISTS idx_paths_domain_date ON top_paths(domain_name, day_date, hits DESC);
        CREATE INDEX IF NOT EXISTS idx_referrers_domain_date ON top_referrers(domain_name, day_date, hits DESC);
        CREATE INDEX IF NOT EXISTS idx_errors_domain_time ON error_logs(domain_name, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_geo_domain_date ON geo_stats(domain_name, day_date, hits DESC);
        """)
        
        # Insert default settings if not exists
        defaults = {
            "geoip_enabled": "0",
            "geoip_db_type": "country",
            "geoip_custom_url": "",
            "retention_days": "60"
        }
        for k, v in defaults.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?);", (k, v))
        
        logger.info("Domain Analytics SQLite database initialized at %s", DB_PATH)
