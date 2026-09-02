"""
plugins/domain_analytics/mmdb_reader.py — Lightweight, pure-Python MaxMind DB (.mmdb) binary reader.
Requires zero external pip libraries. Traverses binary search trees and decodes TLV data maps.
"""
from __future__ import annotations

import struct
import socket
import ipaddress
from pathlib import Path
from typing import Optional, Any, Tuple

METADATA_MARKER = b"\xab\xcd\xefMaxMind.com"


class PureMMDBReader:
    """Zero-dependency pure Python reader for GeoLite2 / MaxMind .mmdb files."""

    def __init__(self, filepath: str | Path):
        self.path = Path(filepath)
        self._raw = self.path.read_bytes()
        self._parse_metadata()

    def _parse_metadata(self) -> None:
        pos = self._raw.rfind(METADATA_MARKER)
        if pos == -1:
            raise ValueError(f"Invalid MMDB: metadata marker not found in {self.path}")
        meta_start = pos + len(METADATA_MARKER)
        meta_data, _ = self._decode(meta_start, meta_start)
        if not isinstance(meta_data, dict):
            raise ValueError("Corrupt MMDB metadata structure")

        self.node_count: int = meta_data.get("node_count", 0)
        self.record_size: int = meta_data.get("record_size", 24)
        self.ip_version: int = meta_data.get("ip_version", 6)
        self.node_byte_size = (self.record_size * 2) // 8
        self.tree_size = self.node_count * self.node_byte_size
        self.data_section_start = self.tree_size + 16

    def _read_node(self, node_number: int, index: int) -> int:
        offset = node_number * self.node_byte_size
        buf = self._raw
        if self.record_size == 24:
            if index == 0:
                return int.from_bytes(buf[offset : offset + 3], "big")
            return int.from_bytes(buf[offset + 3 : offset + 6], "big")
        elif self.record_size == 28:
            mid = buf[offset + 3]
            if index == 0:
                return ((mid & 0xF0) << 20) | int.from_bytes(buf[offset : offset + 3], "big")
            return ((mid & 0x0F) << 24) | int.from_bytes(buf[offset + 4 : offset + 7], "big")
        elif self.record_size == 32:
            if index == 0:
                return int.from_bytes(buf[offset : offset + 4], "big")
            return int.from_bytes(buf[offset + 4 : offset + 8], "big")
        return 0

    def _decode(self, offset: int, data_base: int) -> Tuple[Any, int]:
        ctrl = self._raw[offset]
        offset += 1
        type_num = ctrl >> 5
        size = ctrl & 0x1F

        if type_num == 0:
            type_num = self._raw[offset] + 7
            offset += 1

        if type_num == 1:  # Pointer
            ptr_size = (size >> 3) & 0x03
            if ptr_size == 0:
                new_ptr = ((size & 0x07) << 8) | self._raw[offset]
                offset += 1
            elif ptr_size == 1:
                new_ptr = 2048 + (((size & 0x07) << 16) | int.from_bytes(self._raw[offset : offset + 2], "big"))
                offset += 2
            elif ptr_size == 2:
                new_ptr = 526336 + (((size & 0x07) << 24) | int.from_bytes(self._raw[offset : offset + 3], "big"))
                offset += 3
            else:
                new_ptr = int.from_bytes(self._raw[offset : offset + 4], "big")
                offset += 4
            val, _ = self._decode(data_base + new_ptr, data_base)
            return val, offset

        # Parse size modifier
        if size == 29:
            size = 29 + self._raw[offset]
            offset += 1
        elif size == 30:
            size = 285 + int.from_bytes(self._raw[offset : offset + 2], "big")
            offset += 2
        elif size == 31:
            size = 65821 + int.from_bytes(self._raw[offset : offset + 3], "big")
            offset += 3

        if type_num == 2:  # UTF-8 string
            val = self._raw[offset : offset + size].decode("utf-8", errors="replace")
            return val, offset + size
        elif type_num == 3:  # double
            val = struct.unpack(">d", self._raw[offset : offset + 8])[0]
            return val, offset + 8
        elif type_num == 4:  # bytes
            val = self._raw[offset : offset + size]
            return val, offset + size
        elif type_num in (5, 6, 8, 9, 10):  # int/uint
            val = int.from_bytes(self._raw[offset : offset + size], "big")
            return val, offset + size
        elif type_num == 7:  # map
            d = {}
            for _ in range(size):
                k, offset = self._decode(offset, data_base)
                v, offset = self._decode(offset, data_base)
                d[k] = v
            return d, offset
        elif type_num == 11:  # array
            arr = []
            for _ in range(size):
                item, offset = self._decode(offset, data_base)
                arr.append(item)
            return arr, offset
        elif type_num == 14:  # boolean
            return (size != 0), offset

        return None, offset + size

    def get(self, ip_str: str) -> Optional[dict]:
        """Lookup an IP address in the MMDB tree and return the decoded dictionary record."""
        try:
            ip_obj = ipaddress.ip_address(ip_str.strip())
            if ip_obj.version == 4 and self.ip_version == 6:
                # Map IPv4 to IPv6 (96 zero bits + 32 bits)
                packed = b"\x00" * 12 + socket.inet_pton(socket.AF_INET, ip_str)
            elif ip_obj.version == 4:
                packed = socket.inet_pton(socket.AF_INET, ip_str)
            else:
                packed = socket.inet_pton(socket.AF_INET6, ip_str)
        except Exception:
            return None

        node = 0
        bit_count = len(packed) * 8
        for i in range(bit_count):
            byte_idx = i // 8
            bit_idx = 7 - (i % 8)
            bit = (packed[byte_idx] >> bit_idx) & 1
            node = self._read_node(node, bit)
            if node >= self.node_count:
                break

        if node < self.node_count:
            return None  # Subnet not in database

        # Record is in the data section
        data_offset = self.data_section_start + (node - self.node_count - 16)
        if data_offset >= len(self._raw):
            return None

        record, _ = self._decode(data_offset, self.data_section_start)
        return record if isinstance(record, dict) else None

    def close(self) -> None:
        self._raw = b""
