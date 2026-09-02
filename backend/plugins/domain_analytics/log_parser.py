"""
plugins/domain_analytics/log_parser.py — High-performance incremental Nginx access log parser.
Parses standard combined and extended timing log formats with byte-offset seeking.
"""
from __future__ import annotations

import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from plugins.domain_analytics.models import LogEntry

logger = logging.getLogger(__name__)

# Regex supporting standard Nginx combined + optional rt=... and urt=...
LOG_PATTERN = re.compile(
    r'^(?P<ip>\S+)\s+\S+\s+(?P<user>\S+)\s+\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<method>\S+)\s+(?P<path>[^\s"]+)(?:\s+[^\s"]+)?"\s+'
    r'(?P<status>\d{3})\s+(?P<bytes>[\d\-]+)'
    r'(?:\s+"(?P<referer>[^"]*)")?'
    r'(?:\s+"(?P<agent>[^"]*)")?'
    r'(?:.*?\brt=(?P<rt>[\d\.]+))?'
    r'(?:.*?\burt=(?P<urt>[\d\.\-]+))?',
    re.ASCII
)

FALLBACK_LOG_PATTERN = re.compile(
    r'^(?P<ip>\S+)\s+.*?\[(?P<time>[^\]]+)\]\s+"(?P<method>[A-Za-z]+)\s+(?P<path>[^\s"]+).*?"\s+(?P<status>\d{3})\s+(?P<bytes>[\d\-]+)',
    re.ASCII
)

MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
    "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"
}


def parse_nginx_timestamp(raw_time: str) -> str:
    """Fast conversion of '02/Sep/2026:10:15:30 +0100' to '2026-09-02 10:15:30'."""
    try:
        parts = raw_time.split()[0].split(":")
        date_parts = parts[0].split("/")
        day = date_parts[0].zfill(2)
        month = MONTHS.get(date_parts[1], "01")
        year = date_parts[2]
        return f"{year}-{month}-{day} {parts[1]}:{parts[2]}:{parts[3]}"
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_line(line: str) -> LogEntry | None:
    """Parse a single Nginx log line into a LogEntry dataclass."""
    line = line.strip()
    if not line:
        return None
    match = LOG_PATTERN.match(line)
    if not match:
        match = FALLBACK_LOG_PATTERN.match(line)
        if not match:
            return None
    d = match.groupdict()

    try:
        status = int(d["status"])
        raw_bytes = d.get("bytes") or "0"
        bytes_sent = int(raw_bytes) if raw_bytes.isdigit() else 0
        req_time = float(d["rt"]) if d.get("rt") else 0.0
        
        urt_raw = d.get("urt")
        upstream_time = float(urt_raw) if urt_raw and urt_raw != "-" else 0.0
        
        referer = d.get("referer") or ""
        if referer == "-":
            referer = ""
        
        path = d["path"]
        clean_path = path.split("?")[0] if len(path) > 120 else path

        return LogEntry(
            ip=d["ip"],
            timestamp=parse_nginx_timestamp(d["time"]),
            method=d["method"].upper(),
            path=clean_path,
            status=status,
            bytes_sent=bytes_sent,
            referrer=referer,
            user_agent=d.get("agent") or "",
            request_time=req_time,
            upstream_time=upstream_time,
        )
    except Exception:
        return None


def parse_log_file(
    file_path: Path,
    last_offset: int = 0,
    last_inode: int = 0,
    max_lines: int = 25000,
) -> tuple[list[LogEntry], int, int]:
    """
    Incrementally read log lines from file_path starting at last_offset.
    Detects log rotation via inode or file truncation.
    """
    if not file_path.exists() or not file_path.is_file():
        return [], 0, 0

    try:
        stat = file_path.stat()
        current_inode = stat.st_ino
        current_size = stat.st_size

        # Detect log rotation (inode changed or file size shrunk)
        if (last_inode > 0 and current_inode != last_inode) or current_size < last_offset:
            logger.info("Log rotation detected for %s (size %s < offset %s). Resetting offset.", file_path, current_size, last_offset)
            last_offset = 0

        entries: list[LogEntry] = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            if last_offset > 0:
                f.seek(last_offset)

            count = 0
            for line in f:
                if count >= max_lines:
                    break
                entry = parse_line(line)
                if entry:
                    entries.append(entry)
                count += 1

            new_offset = f.tell()

        return entries, new_offset, current_inode
    except Exception as exc:
        logger.error("Error parsing log file %s: %s", file_path, exc)
        return [], last_offset, last_inode
