"""
services/login_guard.py — In-memory login lockout by IP and username.

Lightweight: process-local dicts, no Redis/DB. Resets on restart.
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from fastapi import Request

_lock = threading.Lock()
# key -> list of failure timestamps (unix)
_ip_fails: dict[str, list[float]] = {}
_user_fails: dict[str, list[float]] = {}
# key -> lockout_until unix
_ip_locked_until: dict[str, float] = {}
_user_locked_until: dict[str, float] = {}

LOCKOUT_MESSAGE = "Too many attempts. Try again later."


def client_ip(request: Request) -> str:
    """Client IP after ProxyHeadersMiddleware (X-Forwarded-For when trusted)."""
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def _prune(times: list[float], window: float, now: float) -> list[float]:
    cutoff = now - window
    return [t for t in times if t >= cutoff]


def is_locked(*, ip: str, username: str) -> bool:
    now = time.time()
    user_key = normalize_username(username)
    with _lock:
        ip_until = _ip_locked_until.get(ip, 0.0)
        if ip_until > now:
            return True
        if ip_until and ip_until <= now:
            _ip_locked_until.pop(ip, None)

        if user_key:
            user_until = _user_locked_until.get(user_key, 0.0)
            if user_until > now:
                return True
            if user_until and user_until <= now:
                _user_locked_until.pop(user_key, None)
    return False


def record_failure(*, ip: str, username: str) -> bool:
    """
    Record a failed login. Returns True if this failure triggered a lockout.
    """
    max_fails = max(1, int(config.LOGIN_MAX_FAILURES))
    lockout = max(1, int(config.LOGIN_LOCKOUT_SECONDS))
    now = time.time()
    user_key = normalize_username(username)
    triggered = False

    with _lock:
        ip_times = _prune(_ip_fails.get(ip, []), lockout, now)
        ip_times.append(now)
        _ip_fails[ip] = ip_times
        if len(ip_times) >= max_fails:
            _ip_locked_until[ip] = now + lockout
            _ip_fails[ip] = []
            triggered = True

        if user_key:
            user_times = _prune(_user_fails.get(user_key, []), lockout, now)
            user_times.append(now)
            _user_fails[user_key] = user_times
            if len(user_times) >= max_fails:
                _user_locked_until[user_key] = now + lockout
                _user_fails[user_key] = []
                triggered = True

    return triggered


def clear_failures(*, ip: str, username: str) -> None:
    user_key = normalize_username(username)
    with _lock:
        _ip_fails.pop(ip, None)
        _ip_locked_until.pop(ip, None)
        if user_key:
            _user_fails.pop(user_key, None)
            _user_locked_until.pop(user_key, None)
