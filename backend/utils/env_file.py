"""
utils/env_file.py — Read/write key=value pairs in the panel .env file.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def env_path() -> Path:
    """Panel install root .env (parent of app/ or backend/)."""
    return Path(__file__).resolve().parent.parent.parent / ".env"


def read_env_map(path: Path | None = None) -> dict[str, str]:
    path = path or env_path()
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        m = _ENV_LINE.match(raw)
        if m:
            result[m.group(1)] = m.group(2)
    return result


def _build_env_text(updates: dict[str, str], path: Path) -> str:
    clean: dict[str, str] = {}
    for k, v in updates.items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            raise ValueError(f"Invalid env key: {k!r}")
        val = "" if v is None else str(v)
        if "\n" in val or "\r" in val:
            raise ValueError(f"Env value for {k} must be a single line")
        clean[k] = val

    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
    else:
        lines = []

    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        m = (
            _ENV_LINE.match(stripped)
            if stripped and not stripped.startswith("#")
            else None
        )
        if m and m.group(1) in clean:
            key = m.group(1)
            out.append(f"{key}={clean[key]}")
            seen.add(key)
        else:
            out.append(line)

    for key, val in clean.items():
        if key not in seen:
            out.append(f"{key}={val}")

    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


async def set_env_values(updates: dict[str, str], path: Path | None = None) -> Path:
    """
    Upsert keys in .env. Preserves unrelated lines and comments.
    Uses shell.write_file so root-owned .env works via sudo tee.
    """
    from utils import shell

    path = path or env_path()
    text = _build_env_text(updates, path)
    await shell.write_file(path, text)
    logger.info("Updated .env keys: %s", ", ".join(sorted(updates)))
    return path
