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
SENSITIVE_PARTS = ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "PRIVATE_KEY", "SALT", "JWT", "KEY_BASE", "AUTH_KEY", "ENCRYPTION_KEY")


def normalize_environment_key(raw_key: Any) -> str:
    """Normalize environment key into safe uppercase identifier ^[A-Z_][A-Z0-9_]{0,127}$."""
    key = str(raw_key or "").strip()
    if not key:
        return ""
    key = re.sub(r"[^A-Za-z0-9_]+", "_", key).upper()
    key = re.sub(r"_+", "_", key)
    key = key.strip("_")
    if not key:
        return ""
    if key[0].isdigit():
        key = f"ENV_{key}"
    return key[:128]


def normalize_environment_value(raw_value: Any) -> str:
    """Normalize environment value to a safe, clean, one-line string."""
    if raw_value is None:
        return ""
    val = str(raw_value).strip()
    if len(val) >= 2:
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")) or (val.startswith("`") and val.endswith("`")):
            val = val[1:-1].strip()
    val = re.sub(r"[\r\n]+", " ", val).strip()
    return val[:4096]


def is_sensitive_key(key: str) -> bool:
    """Detect if a key represents a secret, password, token, or managed credential."""
    upper = key.upper()
    return upper == "DATABASE_URL" or any(part in upper for part in SENSITIVE_PARTS)


def infer_secret_generator(key: str) -> str:
    """Infer the most appropriate generator algorithm for a secret key."""
    upper = key.upper()
    if any(p in upper for p in ("ENCRYPT", "AES", "VAULT", "SIGN")):
        return "base64_32"
    if any(p in upper for p in ("PASSWORD", "PASS", "PASSWD")):
        return "password"
    if any(p in upper for p in ("HEX", "HASH")):
        return "hex32"
    return "urlsafe64"


def normalize_environment_map(values: object) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """
    Sanitizes environment dictionary into:
    1. Clean non-secret uppercase environment variables with single-line values.
    2. Auto-extracted secret requirements for any sensitive keys.
    """
    if values in (None, {}):
        return {}, []
    if not isinstance(values, dict):
        raise ValueError("Environment values must be a dictionary.")
    if len(values) > 64:
        raise ValueError("Too many environment values (maximum 64).")

    clean_envs: dict[str, str] = {}
    extracted_secrets: list[dict[str, Any]] = []

    for raw_k, raw_v in values.items():
        k = normalize_environment_key(raw_k)
        if not k or not ENV_KEY_RE.fullmatch(k):
            continue
        if k == "DATABASE_URL":
            continue
        if is_sensitive_key(k):
            extracted_secrets.append({
                "key": k,
                "purpose": f"Generated {k.lower().replace('_', ' ')}",
                "generator": infer_secret_generator(k),
            })
            continue
        clean_envs[k] = normalize_environment_value(raw_v)

    return clean_envs, extracted_secrets



def parse_requested_keys(value: str | list[str] | None) -> list[str] | None:
    """Return explicit build-secret names; None retains safe legacy selection."""
    if value is None or value == "":
        return None
    if not isinstance(value, (str, list)):
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


def get_declared_secrets(build_root: Path) -> list[str]:
    """Read any secret names declared in the repository's railpack.json."""
    if not build_root.is_dir():
        return []
    config_path = build_root / "railpack.json"
    if not config_path.is_file():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            sec = data.get("secrets", [])
            if isinstance(sec, list):
                return [s for s in sec if isinstance(s, str) and s]
    except Exception:
        pass
    return []
