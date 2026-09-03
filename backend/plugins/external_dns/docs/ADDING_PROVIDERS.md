# Adding an External DNS Provider

The External DNS Manager is built on a **self-registering provider registry**.
Adding a provider (Cloudflare, Route 53, GoDaddy, ...) touches **only** this
plugin's `providers/` package plus locale keys — never the core DNS Manager,
the bridge, the router, the UI, or the database schema.

## 1. Create the adapter

Add `backend/plugins/external_dns/providers/<name>.py`:

```python
from plugins.external_dns.providers.base import (
    Capabilities, CredentialField, DnsProvider, ExternalDnsError,
    NormalizedRecord, ProviderMeta,
)
from plugins.external_dns.providers.registry import register_provider

BASE_URL = "https://api.example.com/v1"   # fixed → no SSRF surface


@register_provider
class ExampleDnsProvider(DnsProvider):
    meta = ProviderMeta(
        id="example",
        label_key="ext_dns_provider_example",       # locale key, NOT raw text
        help_key="ext_dns_provider_example_help",
        icon="network",
        credential_fields=[
            CredentialField(id="token", label_key="ext_dns_cred_example_token", type="password"),
        ],
        supported_types=["A", "AAAA", "CNAME", "MX", "TXT", "NS"],
        capabilities=Capabilities(supports_edit=True, supports_ttl=True, max_values_per_type=0),
    )

    async def verify(self) -> str:
        """Validate credentials + zone; return the canonical zone_ref to store."""

    async def list_records(self) -> list[NormalizedRecord]:
        """Return every record as NormalizedRecord(id, name, type, content, ttl)."""

    async def add_record(self, name, rtype, content, ttl) -> NormalizedRecord: ...
    async def update_record(self, record_id, name, rtype, content, ttl) -> NormalizedRecord: ...
    async def delete_record(self, record_id, name, rtype, content) -> None: ...
```

### Contract notes
- **`NormalizedRecord.id` is opaque and provider-defined.** Use the native record
  id when the provider has one (Hetzner). When it does not (Wix uses rrsets),
  encode a reversible id (e.g. `base64url("host|type|value")`) so edit/delete can
  locate the original. The panel never interprets it.
- **`name`** is shown relative to the zone (`@` for apex, otherwise the prefix).
  Convert to/from the provider's native host format inside the adapter.
- Use `self._client(headers)` (an `httpx.AsyncClient`, 10s timeout) for HTTP.
- Raise `CredentialsError` for auth problems and `ExternalDnsError` for other
  failures — both carry a UI-safe `message` and an HTTP `status_code`.
- `self.validate_credentials()` enforces the declared `credential_fields`;
  override it for extra rules.

## 2. Register the module

Import it in `providers/__init__.py` so the `@register_provider` decorator runs:

```python
from plugins.external_dns.providers import example   # noqa: F401,E402
```

## 3. Add locale keys

In `locales/en.json` (and other locales as needed):

```json
"ext_dns_provider_example": "Example DNS",
"ext_dns_provider_example_help": "Short note shown under the provider selector.",
"ext_dns_cred_example_token": "API Token"
```

That is all. The provider now appears automatically in the connect modal
(provider `<select>` + credential fields are generated from `ProviderMeta`),
is selectable per domain, and its records flow through the DNS Manager.

## Storage & compatibility
Bindings store `provider` (the registry id), `zone_ref`, and encrypted
provider-specific credential JSON. Because these are generic, **no database
migration** is needed for a new provider.
