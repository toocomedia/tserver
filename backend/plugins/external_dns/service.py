"""
plugins/external_dns/service.py — Business logic for the External DNS Manager.

Provider-agnostic: every operation resolves the adapter through the registry
(`registry.get_provider(binding.provider, ...)`), so this module never names a
concrete provider. Credentials are decrypted only in-memory, per operation.

Record operations live in operations.py to keep this file focused on binding
lifecycle + provider metadata (see MODULARITY size guide).
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import Domain
from models.external_dns import ExternalDnsBinding
from plugins.external_dns import crypto
from plugins.external_dns.providers import registry
from plugins.external_dns.providers.base import DnsProvider, ExternalDnsError

logger = logging.getLogger(__name__)


class ExternalDnsService:
    """External DNS provider bindings + record delegation."""

    # -- plugin lifecycle ---------------------------------------------------
    def is_installed(self) -> bool:
        # Pure external-API plugin: no system dependency to probe.
        return True

    # -- provider metadata (registry-driven) --------------------------------
    def providers(self) -> list[dict]:
        return [meta.to_public_dict() for meta in registry.all_metas()]

    def is_known_provider(self, provider_id: str) -> bool:
        return registry.is_known(provider_id)

    def supported_types(self, provider_id: str) -> list[str]:
        cls = registry.get_provider_class(provider_id)
        return list(cls.meta.supported_types) if cls else []

    # -- domain / binding lookups -------------------------------------------
    async def _domain_id(self, db: AsyncSession, domain_name: str) -> int | None:
        dom = (await db.execute(
            select(Domain).where(Domain.name == domain_name)
        )).scalar_one_or_none()
        return dom.id if dom else None

    async def get_binding(self, db: AsyncSession, domain_name: str) -> ExternalDnsBinding | None:
        dom_id = await self._domain_id(db, domain_name)
        if dom_id is None:
            return None
        return (await db.execute(
            select(ExternalDnsBinding).where(ExternalDnsBinding.domain_id == dom_id)
        )).scalar_one_or_none()

    async def list_bindings(self, db: AsyncSession) -> list[dict]:
        """All bindings joined with their domain name (landing page)."""
        rows = (await db.execute(
            select(ExternalDnsBinding, Domain.name)
            .join(Domain, Domain.id == ExternalDnsBinding.domain_id)
            .order_by(Domain.name)
        )).all()
        out = []
        for binding, domain_name in rows:
            public = self.binding_public(binding)
            public["domain"] = domain_name
            out.append(public)
        return out

    def binding_public(self, binding: ExternalDnsBinding | None) -> dict | None:
        """Masked, JSON-safe view of a binding — never exposes plaintext secrets."""
        if binding is None:
            return None
        masked: dict[str, str] = {}
        try:
            raw = crypto.decrypt_dict(binding.credentials_encrypted)
            cls = registry.get_provider_class(binding.provider)
            for f in (cls.meta.credential_fields if cls else []):
                val = str(raw.get(f.id, "") or "")
                masked[f.id] = crypto.mask_secret(val) if f.type == "password" else val
        except Exception as exc:  # corrupt/undecryptable — surface as error, no leak
            logger.warning("Could not mask credentials for binding %s: %s", binding.id, exc)
        return {
            "provider": binding.provider,
            "zone_ref": binding.zone_ref,
            "status": binding.status,
            "last_error": binding.last_error,
            "credentials_masked": masked,
        }

    # -- adapter construction -----------------------------------------------
    def _filter_credentials(self, provider_id: str, credentials: dict) -> dict:
        cls = registry.get_provider_class(provider_id)
        fields = cls.meta.credential_fields if cls else []
        return {f.id: str((credentials or {}).get(f.id, "")).strip() for f in fields}

    def adapter_for(self, binding: ExternalDnsBinding) -> DnsProvider:
        creds = crypto.decrypt_dict(binding.credentials_encrypted)
        return registry.get_provider(binding.provider, creds, binding.zone_ref)

    # -- bind / unbind / test -----------------------------------------------
    async def test_connection(self, provider_id: str, credentials: dict, zone_ref: str) -> dict:
        provider_id = (provider_id or "").strip().lower()
        if not registry.is_known(provider_id):
            raise ExternalDnsError(f"Unknown provider: {provider_id}", status_code=400)
        zone_ref = (zone_ref or "").strip()
        adapter = registry.get_provider(provider_id, credentials, zone_ref)
        adapter.validate_credentials()
        canonical = await adapter.verify()
        return {"ok": True, "zone_ref": canonical or zone_ref}

    async def bind(
        self, db: AsyncSession, domain_name: str, provider_id: str,
        credentials: dict, zone_ref: str,
    ) -> dict:
        """Create-or-update the binding for a domain (verifies before saving)."""
        provider_id = (provider_id or "").strip().lower()
        if not registry.is_known(provider_id):
            raise ExternalDnsError(f"Unknown provider: {provider_id}", status_code=400)
        dom_id = await self._domain_id(db, domain_name)
        if dom_id is None:
            raise ExternalDnsError(f"Domain not found: {domain_name}", status_code=404)

        zone_ref = (zone_ref or "").strip() or domain_name
        existing = (await db.execute(
            select(ExternalDnsBinding).where(ExternalDnsBinding.domain_id == dom_id)
        )).scalar_one_or_none()

        clean_creds = self._filter_credentials(provider_id, credentials)
        # On edit, a blank field keeps the stored secret (same provider only),
        # so admins can change the zone without re-typing every credential.
        if existing is not None and existing.provider == provider_id:
            try:
                old = crypto.decrypt_dict(existing.credentials_encrypted)
                for key, val in clean_creds.items():
                    if not val and old.get(key):
                        clean_creds[key] = old[key]
            except Exception:
                pass

        adapter = registry.get_provider(provider_id, clean_creds, zone_ref)
        adapter.validate_credentials()
        canonical_zone = await adapter.verify()   # raises ExternalDnsError on bad creds/zone

        binding = existing or ExternalDnsBinding(domain_id=dom_id)
        if existing is None:
            db.add(binding)
        binding.provider = provider_id
        binding.zone_ref = canonical_zone or zone_ref
        binding.credentials_encrypted = crypto.encrypt_dict(clean_creds)
        binding.status = "active"
        binding.last_error = None
        await db.flush()
        logger.info("External DNS bound: %s → %s", domain_name, provider_id)
        return self.binding_public(binding)

    async def unbind(self, db: AsyncSession, domain_name: str) -> None:
        binding = await self.get_binding(db, domain_name)
        if binding is not None:
            await db.delete(binding)
            await db.flush()
            logger.info("External DNS unbound: %s", domain_name)

    async def set_status(
        self, db: AsyncSession, binding: ExternalDnsBinding, status: str, error: str | None
    ) -> None:
        """Persist a health change only when it actually differs (avoid write churn)."""
        if binding.status != status or (binding.last_error or None) != (error or None):
            binding.status = status
            binding.last_error = error
            await db.flush()


external_dns_service = ExternalDnsService()
