"""
middleware/limiter.py — Shared slowapi Limiter (in-memory, no Redis).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])
