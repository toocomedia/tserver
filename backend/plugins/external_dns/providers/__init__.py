"""
plugins/external_dns/providers/__init__.py — Import adapters so they self-register.

Importing this package runs each adapter's @register_provider decorator and
populates the registry. To add a provider: create its adapter module, then add
one import line below (plus its locale keys). Nothing else changes.
"""
from plugins.external_dns.providers import base       # noqa: F401
from plugins.external_dns.providers import registry   # noqa: F401

# --- Adapter modules (import order does not matter) ---
from plugins.external_dns.providers import hetzner    # noqa: F401,E402

__all__ = ["base", "registry", "hetzner"]
