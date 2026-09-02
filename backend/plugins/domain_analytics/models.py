"""
plugins/domain_analytics/models.py — Data classes and models for domain analytics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Dict, Any


@dataclass
class LogEntry:
    ip: str
    timestamp: str  # YYYY-MM-DD HH:MM:SS
    method: str
    path: str
    status: int
    bytes_sent: int
    referrer: str
    user_agent: str
    request_time: float  # seconds
    upstream_time: float  # seconds


@dataclass
class DomainSummary:
    domain_name: str
    is_active: bool
    total_requests_24h: int
    unique_ips_24h: int
    bandwidth_bytes_24h: int
    error_rate_24h: float
    avg_response_time_ms: float


@dataclass
class GeoLocation:
    country_code: str
    country_name: str
    city_name: Optional[str] = None


@dataclass
class AnalyticsSettings:
    geoip_enabled: bool
    geoip_db_type: str  # 'country' | 'city' | 'custom'
    geoip_custom_url: str
    retention_days: int
    geoip_db_installed: bool = False
    geoip_db_path: str = ""
