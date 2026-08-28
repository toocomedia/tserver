"""Shared application-level runtime classification outside snapshot deployment."""
from __future__ import annotations

from typing import Any

COMPOSE_DEPLOY_TYPES = frozenset({"official_stack", "app_spec"})


def is_compose_app(app: Any) -> bool:
    return getattr(app, "deploy_type", None) in COMPOSE_DEPLOY_TYPES

