"""Versioned snapshot envelope helpers for mixed legacy and AppSpec deployments."""
from __future__ import annotations

import json
from typing import Any

from services.apps_engine.app_spec import AppSpec
from services.apps_engine.app_spec_codec import app_spec_from_dict, app_spec_to_dict

SCHEMA_VERSION = 2
COMPOSE_RUNTIME_KIND = "compose"


def compose_envelope(spec: AppSpec) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "runtime_kind": COMPOSE_RUNTIME_KIND,
        "app_spec": app_spec_to_dict(spec),
    }


def decode(config_json: str) -> dict[str, Any]:
    try:
        value = json.loads(config_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Deployment snapshot configuration is invalid.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Deployment snapshot configuration must be an object.")
    return value


def runtime_kind(config_json: str) -> str:
    value = decode(config_json)
    if value.get("schema_version") == SCHEMA_VERSION and value.get("runtime_kind") == COMPOSE_RUNTIME_KIND:
        return COMPOSE_RUNTIME_KIND
    return "legacy"


def app_spec(config_json: str) -> AppSpec:
    value = decode(config_json)
    if value.get("schema_version") != SCHEMA_VERSION or value.get("runtime_kind") != COMPOSE_RUNTIME_KIND:
        raise RuntimeError("Deployment snapshot is not a Compose AppSpec snapshot.")
    raw = value.get("app_spec")
    if not isinstance(raw, dict):
        raise RuntimeError("Compose AppSpec snapshot has no valid application specification.")
    return app_spec_from_dict(raw)


def replace_app_spec(config_json: str, spec: AppSpec) -> str:
    value = decode(config_json)
    if value.get("schema_version") != SCHEMA_VERSION or value.get("runtime_kind") != COMPOSE_RUNTIME_KIND:
        raise RuntimeError("Deployment snapshot is not a Compose AppSpec snapshot.")
    value["app_spec"] = app_spec_to_dict(spec)
    return json.dumps(value, sort_keys=True)

