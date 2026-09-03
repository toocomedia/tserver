"""
plugins/external_dns/providers/hetzner.py — Hetzner DNS API adapter.

API:  https://dns.hetzner.com/api/v1
Auth: `Auth-API-Token` header (token created in the Hetzner Console).
Model: per-record CRUD with native record ids → NormalizedRecord.id = record id.
Zone locator: `zone_ref` stores the resolved zone id (verify() resolves a name).
"""
from __future__ import annotations

import logging

from plugins.external_dns.providers.base import (
    Capabilities,
    CredentialField,
    CredentialsError,
    DnsProvider,
    ExternalDnsError,
    NormalizedRecord,
    ProviderMeta,
)
from plugins.external_dns.providers.registry import register_provider

logger = logging.getLogger(__name__)

BASE_URL = "https://dns.hetzner.com/api/v1"
SUPPORTED_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA", "TLSA"]


@register_provider
class HetznerDnsProvider(DnsProvider):
    meta = ProviderMeta(
        id="hetzner",
        label_key="ext_dns_provider_hetzner",
        help_key="ext_dns_provider_hetzner_help",
        icon="network",
        credential_fields=[
            CredentialField(
                id="token",
                label_key="ext_dns_cred_hetzner_token",
                type="password",
                help_key="ext_dns_cred_hetzner_token_help",
            ),
        ],
        supported_types=SUPPORTED_TYPES,
        capabilities=Capabilities(supports_edit=True, supports_ttl=True, max_values_per_type=0),
        setup_url="https://dns.hetzner.com/",
        setup_label_key="ext_dns_setup_hetzner",
    )

    def _headers(self) -> dict[str, str]:
        return {
            "Auth-API-Token": str(self.credentials.get("token", "")).strip(),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _looks_like_zone_id(ref: str) -> bool:
        ref = (ref or "").strip().lower()
        return len(ref) == 32 and all(ch in "0123456789abcdef" for ch in ref)

    def _clean_name(self, name: str) -> str:
        """Panel sends '@' for apex or a subdomain prefix; Hetzner uses '@' too."""
        n = (name or "").strip().rstrip(".")
        return "@" if n in ("", "@") else n

    def _normalize(self, rec: dict) -> NormalizedRecord:
        return NormalizedRecord(
            id=str(rec.get("id", "")),
            name=(rec.get("name") or "").rstrip("."),
            type=(rec.get("type") or "").upper(),
            content=rec.get("value") or "",
            ttl=int(rec.get("ttl") or 3600),
        )

    def _raise_for(self, r, action: str, ok: tuple[int, ...] = (200, 201)) -> None:
        if r.status_code in (401, 403):
            raise CredentialsError("Hetzner rejected the API token.")
        if r.status_code not in ok:
            raise ExternalDnsError(
                f"Hetzner {action} failed ({r.status_code}): {(r.text or '')[:300]}"
            )

    async def _resolve_zone_id(self) -> str:
        ref = self.zone_ref.strip().lower()
        async with self._client(self._headers()) as c:
            r = await c.get(f"{BASE_URL}/zones", params={"name": ref})
        self._raise_for(r, "zone lookup", ok=(200,))
        zones = (r.json() or {}).get("zones", []) or []
        for z in zones:
            if (z.get("name") or "").rstrip(".").lower() == ref:
                return str(z.get("id", ""))
        return str(zones[0]["id"]) if zones else ""

    async def _zone_id(self) -> str:
        if self._looks_like_zone_id(self.zone_ref):
            return self.zone_ref.strip()
        zone_id = await self._resolve_zone_id()
        if not zone_id:
            raise ExternalDnsError(
                f"Hetzner zone not found for '{self.zone_ref}'.", status_code=404
            )
        return zone_id

    async def verify(self) -> str:
        self.validate_credentials()
        zone_id = await self._zone_id()
        return zone_id

    async def list_records(self) -> list[NormalizedRecord]:
        zone_id = await self._zone_id()
        rows: list[NormalizedRecord] = []
        page = 1
        async with self._client(self._headers()) as c:
            while True:
                r = await c.get(
                    f"{BASE_URL}/records",
                    params={"zone_id": zone_id, "per_page": 100, "page": page},
                )
                self._raise_for(r, "list records", ok=(200,))
                data = r.json() or {}
                batch = data.get("records", []) or []
                rows.extend(self._normalize(rec) for rec in batch)
                pagination = (data.get("meta", {}) or {}).get("pagination", {}) or {}
                last_page = int(pagination.get("last_page", page) or page)
                if page >= last_page or not batch:
                    break
                page += 1
        rows.sort(key=lambda x: (x.name, x.type))
        return rows

    async def add_record(self, name: str, rtype: str, content: str, ttl: int) -> NormalizedRecord:
        zone_id = await self._zone_id()
        payload = {
            "zone_id": zone_id,
            "type": rtype.strip().upper(),
            "name": self._clean_name(name),
            "value": content,
            "ttl": int(ttl or 3600),
        }
        async with self._client(self._headers()) as c:
            r = await c.post(f"{BASE_URL}/records", json=payload)
        self._raise_for(r, "create record")
        return self._normalize((r.json() or {}).get("record", {}))

    async def update_record(
        self, record_id: str, name: str, rtype: str, content: str, ttl: int
    ) -> NormalizedRecord:
        payload = {
            "type": rtype.strip().upper(),
            "name": self._clean_name(name),
            "value": content,
            "ttl": int(ttl or 3600),
        }
        async with self._client(self._headers()) as c:
            r = await c.put(f"{BASE_URL}/records/{record_id}", json=payload)
        self._raise_for(r, "update record")
        return self._normalize((r.json() or {}).get("record", {}))

    async def delete_record(self, record_id: str, name: str, rtype: str, content: str) -> None:
        async with self._client(self._headers()) as c:
            r = await c.delete(f"{BASE_URL}/records/{record_id}")
        self._raise_for(r, "delete record", ok=(200, 204))
