"""Build-secret selection and Railpack configuration updates."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
RUNTIME_METADATA_KEYS = {"PORT", "INTERNAL_PORT", "HOST_PORT", "CONTAINER_PORT"}
LEGACY_SECRET_NAMES = {"DATABASE_URL", "REDIS_URL", "MYSQL_URL", "MONGODB_URL"}
LEGACY_SECRET_SUFFIXES = ("_SECRET", "_TOKEN", "_PASSWORD", "_API_KEY", "_PRIVATE_KEY")


def parse_requested_keys(value: str | list[str] | None) -> list[str] | None:
    """Return explicit build-secret names; None retains safe legacy selection."""
    if value is None or value == "":
        return None
    raw = json.loads(value) if isinstance(value, str) else value
    if not isinstance(raw, list) or len(raw) > 32:
        raise ValueError("Build secret keys must be a list of at most 32 environment names.")
    keys = list(dict.fromkeys(raw))
    if any(not isinstance(key, str) or not ENV_KEY_RE.fullmatch(key) for key in keys):
        raise ValueError("Build secret keys must use safe uppercase environment names.")
    return keys


def select_names(values: dict[str, str], requested: str | list[str] | None) -> list[str]:
    explicit = parse_requested_keys(requested)
    if explicit is not None:
        missing = [key for key in explicit if key not in values]
        if missing:
            raise ValueError(f"Build secret values are missing: {', '.join(missing)}.")
        return [key for key in explicit if key not in RUNTIME_METADATA_KEYS]
    return [
        key for key in values
        if key not in RUNTIME_METADATA_KEYS
        and (key in LEGACY_SECRET_NAMES or key.endswith(LEGACY_SECRET_SUFFIXES))
    ]


def inject_railpack_secrets(build_root: Path, secret_names: list[str]) -> None:
    """Atomically merge approved names into the ephemeral Railpack config."""
    if not secret_names:
        return
    if not build_root.is_dir():
        raise RuntimeError("Build workspace is unavailable.")
    config_path = build_root / "railpack.json"
    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            parsed = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("railpack.json is invalid JSON.") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("railpack.json must contain a JSON object.")
        data = parsed
    existing = data.get("secrets", [])
    if not isinstance(existing, list) or any(not isinstance(key, str) for key in existing):
        raise RuntimeError("railpack.json secrets must be a list of environment names.")
    merged = list(dict.fromkeys([*existing, *secret_names]))
    data["secrets"] = merged
    temporary = config_path.with_name(f".{config_path.name}.srv-panel")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, config_path)
