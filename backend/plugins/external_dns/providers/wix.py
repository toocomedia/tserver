"""
plugins/external_dns/providers/wix.py — Wix Domain DNS API adapter.

API:  https://www.wixapis.com/domains/v1/dns-zones/{domainName}
Auth: account-level API key (`Authorization`) + `wix-account-id` header.
Model: rrsets — one object per (hostName, type) with a `values[]` array, changed
only via PATCH additions/deletions (see wix_records.py). There are no per-record
ids, so NormalizedRecord.id is a reversible base64url of "host|type|value".

Constraint: Wix can only manage DNS for domains registered via Wix or external
domains connected by nameservers to Wix (not "pointing" connections).
"""
from __future__ import annotations

import base64
import logging

from plugins.external_dns.providers import wix_records as rr
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

BASE_URL = "https://www.wixapis.com/domains/v1/dns-zones"
SUPPORTED_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "SPF", "SRV", "NS"]


def encode_id(host: str, rtype: str, value: str) -> str:
    raw = f"{host}|{rtype}|{value}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_id(record_id: str) -> tuple[str, str, str]:
    s = (record_id or "").strip()
    if not s:
        return "", "", ""
    try:
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")
    except Exception:
        return "", "", ""
    parts = raw.split("|", 2)
    return (parts[0], parts[1], parts[2]) if len(parts) == 3 else ("", "", "")


@register_provider
class WixDnsProvider(DnsProvider):
    meta = ProviderMeta(
        id="wix",
        label_key="ext_dns_provider_wix",
        help_key="ext_dns_provider_wix_help",
        icon="network",
        credential_fields=[
            CredentialField(
                id="api_key",
                label_key="ext_dns_cred_wix_api_key",
                type="password",
                help_key="ext_dns_cred_wix_api_key_help",
            ),
            CredentialField(
                id="account_id",
                label_key="ext_dns_cred_wix_account_id",
                type="text",
                help_key="ext_dns_cred_wix_account_id_help",
            ),
        ],
        supported_types=SUPPORTED_TYPES,
        capabilities=Capabilities(supports_edit=True, supports_ttl=True, max_values_per_type=50),
        setup_url="https://dev.wix.com/",
        setup_label_key="ext_dns_setup_wix",
    )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": str(self.credentials.get("api_key", "")).strip(),
            "wix-account-id": str(self.credentials.get("account_id", "")).strip(),
            "Content-Type": "application/json",
        }

    def _zone_url(self) -> str:
        return f"{BASE_URL}/{self.zone_ref.strip()}"

    def _max(self) -> int:
        return self.meta.capabilities.max_values_per_type

    def _fqdn(self, name: str) -> str:
        """'@'/'www' (or an FQDN) → Wix hostName (FQDN, lowercased)."""
        n = (name or "").strip().rstrip(".").lower()
        domain = self.zone_ref.strip().lower()
        if n in ("", "@"):
            return domain
        if n == domain or n.endswith("." + domain):
            return n
        return f"{n}.{domain}"

    def _short(self, host: str) -> str:
        """Wix hostName (FQDN) → '@'/prefix relative to the zone, for display."""
        h = (host or "").strip().rstrip(".").lower()
        domain = self.zone_ref.strip().lower()
        if not domain or h == domain:
            return "@" if h == domain else h
        if h.endswith("." + domain):
            return h[: -(len(domain) + 1)] or "@"
        return h

    async def _get_zone(self) -> dict:
        async with self._client(self._headers()) as c:
            r = await c.get(self._zone_url())
        if r.status_code in (401, 403):
            raise CredentialsError("Wix rejected the API key or account id.")
        if r.status_code == 404:
            raise ExternalDnsError(
                f"Wix has no DNS zone for '{self.zone_ref}'. The domain must be registered "
                "via Wix or connected by nameservers.",
                status_code=404,
            )
        if r.status_code != 200:
            raise ExternalDnsError(f"Wix zone lookup failed ({r.status_code}).")
        data = r.json() or {}
        return data.get("dnsZone", data) or {}

    async def _patch(self, additions: list[dict], deletions: list[dict]) -> None:
        body: dict = {}
        if additions:
            body["additions"] = additions
        if deletions:
            body["deletions"] = deletions
        if not body:
            return
        async with self._client(self._headers()) as c:
            r = await c.patch(self._zone_url(), json=body)
        if r.status_code in (401, 403):
            raise CredentialsError("Wix rejected the API key or account id.")
        if r.status_code not in (200, 204):
            raise ExternalDnsError(
                f"Wix record update failed ({r.status_code}): {(r.text or '')[:300]}"
            )

    async def verify(self) -> str:
        self.validate_credentials()
        zone = await self._get_zone()
        return (zone.get("domainName") or self.zone_ref).strip().lower()

    async def list_records(self) -> list[NormalizedRecord]:
        zone = await self._get_zone()
        rows: list[NormalizedRecord] = []
        for rec in zone.get("records", []) or []:
            rtype = (rec.get("type") or "").upper()
            host = rec.get("hostName") or ""
            ttl = int(rec.get("ttl") or 3600)
            for value in rec.get("values", []) or []:
                rows.append(NormalizedRecord(
                    id=encode_id(host, rtype, value),
                    name=self._short(host),
                    type=rtype,
                    content=value,
                    ttl=ttl,
                ))
        rows.sort(key=lambda x: (x.name, x.type))
        return rows

    async def add_record(self, name: str, rtype: str, content: str, ttl: int) -> NormalizedRecord:
        host = self._fqdn(name)
        rtype = rtype.strip().upper()
        records = (await self._get_zone()).get("records", []) or []
        additions, deletions = rr.plan_add(records, host, rtype, content, int(ttl or 3600), self._max())
        await self._patch(additions, deletions)
        return NormalizedRecord(encode_id(host, rtype, content), self._short(host), rtype, content, int(ttl or 3600))

    async def update_record(
        self, record_id: str, name: str, rtype: str, content: str, ttl: int
    ) -> NormalizedRecord:
        old_host, old_type, old_value = decode_id(record_id)
        new_host = self._fqdn(name)
        rtype = rtype.strip().upper()
        if not old_host:                       # id missing/undecodable → fall back to hints
            old_host, old_type, old_value = new_host, rtype, content
        records = (await self._get_zone()).get("records", []) or []
        additions, deletions = rr.plan_update(
            records, old_host, old_type, old_value,
            new_host, rtype, content, int(ttl or 3600), self._max(),
        )
        await self._patch(additions, deletions)
        return NormalizedRecord(encode_id(new_host, rtype, content), self._short(new_host), rtype, content, int(ttl or 3600))

    async def delete_record(self, record_id: str, name: str, rtype: str, content: str) -> None:
        host, type_, value = decode_id(record_id)
        if not host:
            host, type_, value = self._fqdn(name), rtype.strip().upper(), content
        records = (await self._get_zone()).get("records", []) or []
        additions, deletions = rr.plan_delete_value(records, host, type_, value)
        await self._patch(additions, deletions)
