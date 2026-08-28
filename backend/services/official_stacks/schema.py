"""Compatibility exports for legacy Official Stack readers."""
from __future__ import annotations

from typing import Any

from services.apps_engine.app_spec import (
    AppSpec,
    ConfigFileSpec,
    HealthCheckSpec,
    SecretRequirement,
    ServiceSpec,
    VolumeSpec,
)
from services.apps_engine.app_spec_codec import app_spec_from_dict, legacy_app_spec_to_dict

OfficialStackDefinition = AppSpec
ServiceDefinition = ServiceSpec
VolumeDefinition = VolumeSpec
ConfigFileDefinition = ConfigFileSpec
HealthCheckDefinition = HealthCheckSpec


def stack_to_dict(stack: AppSpec) -> dict[str, Any]:
    return legacy_app_spec_to_dict(stack)


def stack_from_dict(data: dict[str, Any]) -> AppSpec:
    return app_spec_from_dict(data, allow_legacy_secret_defaults=True)


__all__ = [
    "OfficialStackDefinition",
    "ServiceDefinition",
    "VolumeDefinition",
    "ConfigFileDefinition",
    "HealthCheckDefinition",
    "SecretRequirement",
    "stack_to_dict",
    "stack_from_dict",
]
