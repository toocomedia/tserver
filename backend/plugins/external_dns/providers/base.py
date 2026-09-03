"""
plugins/external_dns/providers/base.py — Provider-agnostic external DNS contracts.

Every external DNS adapter subclasses DnsProvider and normalizes its native
record model into NormalizedRecord rows, so the panel UI, service, router, and
core DNS pages never need to know which provider is in use.

Adding a provider = a new subclass decorated with @register_provider that
declares its own ProviderMeta (see docs/ADDING_PROVIDERS.md). Nothing else in
the plugin or the panel changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx


class ExternalDnsError(Exception):
    """Base error for external DNS operations; message is safe to show in the UI."""

    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class CredentialsError(ExternalDnsError):
    """Invalid/missing credentials, or the provider rejected authentication."""

    def __init__(self, message: str):
        super().__init__(message, status_code=400)


@dataclass
class NormalizedRecord:
    """A single DNS record row, provider-agnostic.

    `id` is opaque and provider-defined: a native record id where the provider
    has one (Hetzner), otherwise a reversible/stable token (Wix). The UI carries
    it back verbatim for edit/delete; only the owning adapter interprets it.
    """

    id: str
    name: str
    type: str
    content: str
    ttl: int = 3600

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "content": self.content,
            "ttl": self.ttl,
        }


@dataclass
class CredentialField:
    """One credential input; the bind modal renders these dynamically."""

    id: str
    label_key: str
    type: str = "password"          # password | text
    required: bool = True
    help_key: str | None = None
    placeholder: str = ""


@dataclass
class Capabilities:
    """What a provider supports — the UI adapts instead of branching per provider."""

    supports_edit: bool = True
    supports_ttl: bool = True
    max_values_per_type: int = 0    # 0 = unlimited


@dataclass
class ProviderMeta:
    """Static, provider-declared metadata. Carries i18n KEYS, never raw strings."""

    id: str
    label_key: str
    help_key: str | None
    icon: str
    credential_fields: list[CredentialField]
    supported_types: list[str]
    capabilities: Capabilities = field(default_factory=Capabilities)
    setup_url: str | None = None            # external "get API credentials" link
    setup_label_key: str | None = None      # label for that link (i18n key)

    def to_public_dict(self) -> dict[str, Any]:
        """JSON-safe metadata for /api/providers (no secrets — keys/ids only)."""
        return {
            "id": self.id,
            "label_key": self.label_key,
            "help_key": self.help_key,
            "icon": self.icon,
            "setup_url": self.setup_url,
            "setup_label_key": self.setup_label_key,
            "credential_fields": [
                {
                    "id": f.id,
                    "label_key": f.label_key,
                    "type": f.type,
                    "required": f.required,
                    "help_key": f.help_key,
                    "placeholder": f.placeholder,
                }
                for f in self.credential_fields
            ],
            "supported_types": list(self.supported_types),
            "capabilities": {
                "supports_edit": self.capabilities.supports_edit,
                "supports_ttl": self.capabilities.supports_ttl,
                "max_values_per_type": self.capabilities.max_values_per_type,
            },
        }


class DnsProvider(ABC):
    """Abstract external DNS adapter.

    Instantiated per operation with decrypted credentials + the stored zone
    locator. All methods are async and raise ExternalDnsError on failure. Base
    URLs are fixed per adapter (never user-supplied) so there is no SSRF surface.
    """

    #: Subclasses must set this to their ProviderMeta.
    meta: ProviderMeta

    def __init__(self, credentials: dict[str, str], zone_ref: str):
        self.credentials = credentials or {}
        self.zone_ref = (zone_ref or "").strip()

    # -- metadata / validation ---------------------------------------------
    def validate_credentials(self) -> None:
        """Ensure every required credential field is present. Override for extras."""
        missing = [
            f.id for f in self.meta.credential_fields
            if f.required and not str(self.credentials.get(f.id, "")).strip()
        ]
        if missing:
            raise CredentialsError(f"Missing required credentials: {', '.join(missing)}.")

    # -- HTTP helper --------------------------------------------------------
    def _client(self, headers: dict[str, str]) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=headers, timeout=10.0)

    # -- contract -----------------------------------------------------------
    @abstractmethod
    async def verify(self) -> str:
        """Validate credentials + zone; return the canonical zone_ref to persist."""

    @abstractmethod
    async def list_records(self) -> list[NormalizedRecord]:
        """Return every record normalized into rows."""

    @abstractmethod
    async def add_record(self, name: str, rtype: str, content: str, ttl: int) -> NormalizedRecord:
        """Create a record and return the normalized row (with its opaque id)."""

    @abstractmethod
    async def update_record(
        self, record_id: str, name: str, rtype: str, content: str, ttl: int
    ) -> NormalizedRecord:
        """Update the record identified by its opaque id; return the new row."""

    @abstractmethod
    async def delete_record(self, record_id: str, name: str, rtype: str, content: str) -> None:
        """Delete the record identified by its opaque id (name/type/content as hints)."""
