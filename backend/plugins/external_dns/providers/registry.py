"""
plugins/external_dns/providers/registry.py — Self-registering provider registry.

Adapters decorate themselves with @register_provider; providers/__init__.py
imports every adapter module so the registry self-populates at import time.
The service, router, and UI look providers up here and never hardcode a name —
which is what makes a new provider a drop-in addition.
"""
from __future__ import annotations

import logging
from typing import Type

from plugins.external_dns.providers.base import DnsProvider, ProviderMeta

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, Type[DnsProvider]] = {}


def register_provider(cls: Type[DnsProvider]) -> Type[DnsProvider]:
    """Class decorator: register a DnsProvider subclass under its meta.id."""
    meta: ProviderMeta | None = getattr(cls, "meta", None)
    provider_id = getattr(meta, "id", None)
    if not provider_id:
        raise ValueError(f"{cls.__name__} must define a ProviderMeta with an id.")
    if provider_id in _PROVIDERS:
        raise ValueError(f"Duplicate external DNS provider id: {provider_id}")
    _PROVIDERS[provider_id] = cls
    logger.debug("Registered external DNS provider: %s", provider_id)
    return cls


def get_provider_class(provider_id: str) -> Type[DnsProvider] | None:
    """Return the adapter class for an id, or None if unknown."""
    return _PROVIDERS.get((provider_id or "").strip().lower())


def get_provider(provider_id: str, credentials: dict, zone_ref: str) -> DnsProvider:
    """Instantiate an adapter, or raise ValueError for an unknown provider id."""
    cls = get_provider_class(provider_id)
    if cls is None:
        raise ValueError(f"Unknown external DNS provider: {provider_id}")
    return cls(credentials, zone_ref)


def is_known(provider_id: str) -> bool:
    return get_provider_class(provider_id) is not None


def all_provider_classes() -> list[Type[DnsProvider]]:
    return list(_PROVIDERS.values())


def all_metas() -> list[ProviderMeta]:
    return [cls.meta for cls in _PROVIDERS.values()]


def provider_ids() -> list[str]:
    return list(_PROVIDERS.keys())
