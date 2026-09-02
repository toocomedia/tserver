"""
tests/test_domain_analytics.py — Unit & Integration tests for Domain Analytics Plugin.
"""
import os
import tempfile
import pytest
from pathlib import Path

from plugins.domain_analytics.db import init_db, get_db
from plugins.domain_analytics.log_parser import parse_line, parse_log_file, parse_nginx_timestamp
from plugins.domain_analytics.aggregator import process_domain_entries, prune_old_data
from plugins.domain_analytics.geoip_service import geoip_service
from plugins.domain_analytics.service import domain_analytics_service


SAMPLE_LOG_LINES = [
    '93.184.216.34 - - [02/Sep/2026:10:15:30 +0000] "GET /index.html HTTP/1.1" 200 4520 "https://google.com" "Mozilla/5.0" rt=0.045 urt=0.038',
    '93.184.216.34 - - [02/Sep/2026:10:16:00 +0000] "GET /about HTTP/1.1" 200 1200 "-" "Mozilla/5.0" rt=0.020 urt=0.015',
    '198.51.100.22 - - [02/Sep/2026:10:17:15 +0000] "GET /missing-page HTTP/1.1" 404 180 "https://twitter.com" "Mozilla/5.0" rt=0.005 urt=-',
    '203.0.113.50 - - [02/Sep/2026:10:18:22 +0000] "POST /api/crash HTTP/1.1" 500 520 "-" "curl/7.68.0" rt=0.850 urt=0.840',
]


def test_db_initialization():
    init_db()
    with get_db() as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        table_names = {t["name"] for t in tables}
        assert "tracked_domains" in table_names
        assert "hourly_stats" in table_names
        assert "top_paths" in table_names
        assert "top_referrers" in table_names
        assert "error_logs" in table_names
        assert "settings" in table_names


def test_log_parser_line():
    entry = parse_line(SAMPLE_LOG_LINES[0])
    assert entry is not None
    assert entry.ip == "93.184.216.34"
    assert entry.method == "GET"
    assert entry.path == "/index.html"
    assert entry.status == 200
    assert entry.bytes_sent == 4520
    assert entry.referrer == "https://google.com"
    assert entry.request_time == 0.045
    assert entry.upstream_time == 0.038

    # 404 line
    entry_404 = parse_line(SAMPLE_LOG_LINES[2])
    assert entry_404 is not None
    assert entry_404.status == 404
    assert entry_404.path == "/missing-page"

    # Malformed line returns None safely
    assert parse_line("invalid gibberish line") is None


def test_incremental_log_file_parsing():
    with tempfile.NamedTemporaryFile(mode="w+", delete=False, encoding="utf-8") as tmp:
        tmp.write("\n".join(SAMPLE_LOG_LINES[:2]) + "\n")
        tmp.flush()
        tmp_path = Path(tmp.name)

    try:
        # First read: 2 lines
        entries, offset1, inode1 = parse_log_file(tmp_path, last_offset=0)
        assert len(entries) == 2
        assert offset1 > 0

        # Second read with no new data: 0 lines
        entries_empty, offset2, _ = parse_log_file(tmp_path, last_offset=offset1, last_inode=inode1)
        assert len(entries_empty) == 0
        assert offset2 == offset1

        # Append 2 more lines
        with open(tmp_path, "a", encoding="utf-8") as f:
            f.write("\n".join(SAMPLE_LOG_LINES[2:]) + "\n")

        # Third read: reads only the 2 newly appended lines
        entries_new, offset3, _ = parse_log_file(tmp_path, last_offset=offset1, last_inode=inode1)
        assert len(entries_new) == 2
        assert entries_new[0].status == 404
        assert entries_new[1].status == 500
        assert offset3 > offset1
    finally:
        tmp_path.unlink(missing_ok=True)


def test_aggregation_and_summary():
    init_db()
    test_domain = "unique-test-domain.com"
    with get_db() as conn:
        conn.execute("DELETE FROM hourly_stats WHERE domain_name = ?", (test_domain,))
        conn.execute("DELETE FROM top_paths WHERE domain_name = ?", (test_domain,))
        conn.execute("DELETE FROM top_referrers WHERE domain_name = ?", (test_domain,))
        conn.execute("DELETE FROM error_logs WHERE domain_name = ?", (test_domain,))
        conn.execute("DELETE FROM tracked_domains WHERE domain_name = ?", (test_domain,))

    domain_analytics_service.toggle_domain(test_domain, is_active=True)

    entries = [parse_line(line) for line in SAMPLE_LOG_LINES if parse_line(line)]
    process_domain_entries(test_domain, entries)

    detail = domain_analytics_service.get_domain_detail(test_domain, days=1)
    assert detail["totals"]["requests"] == 4
    assert detail["totals"]["unique_ips"] == 3
    assert detail["totals"]["status_2xx"] == 2
    assert detail["totals"]["status_4xx"] == 1
    assert detail["totals"]["status_5xx"] == 1
    assert len(detail["top_paths"]) >= 3
    assert len(detail["top_referrers"]) >= 1
    assert len(detail["recent_errors"]) == 2


def test_geoip_service_toggle():
    geoip_service.set_enabled(False)
    assert geoip_service.is_enabled() is False
    assert geoip_service.lookup("8.8.8.8") is None

    geoip_service.set_enabled(True)
    assert geoip_service.is_enabled() is True
    # If no mmdb installed, lookup returns None safely without crashing
    assert geoip_service.lookup("8.8.8.8") is None

    # Reset
    geoip_service.set_enabled(False)


def test_data_retention_prune():
    init_db()
    # Prune should run cleanly without errors
    deleted = prune_old_data(retention_days=30)
    assert isinstance(deleted, int)
