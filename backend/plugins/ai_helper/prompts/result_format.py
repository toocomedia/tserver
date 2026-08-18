"""
prompts/result_format.py — Required output format rules for UI card rendering.
Teaches the AI the exact markdown syntax that triggers interactive card strips in the chat UI.
"""
from __future__ import annotations

RESULT_FORMAT_RULES = """### Structured Output Formats (REQUIRED — UI renders these as interactive cards):

**File/Directory Listings** → triggers Directory Tree Explorer card:
```
- [DIR] dirname/
- [FILE] filename.ext (12 KB)
- [FILE] config.json (2 KB)
```

**Records & Overviews** → triggers structured table card:
```
| Field | Value |
|---|---|
| Status | running |
| Port | 9100 |
```

**Log Output** → triggers scrollable log card:
```log
[2026-01-01 12:00:00] INFO: Server started
[2026-01-01 12:00:01] ERROR: Connection refused
```

**Security Audit Findings** → triggers coloured security card:
```security
[CRITICAL] No rate limiting detected on authentication endpoints
[WARNING] Nginx proxy shows nginx_active: false — verify configuration
[WARNING] DNS records not managed by panel (external DNS)
[OK] SSL certificate valid until 2026-10-31
[OK] API docs disabled in production (openapi_url=None)
[OK] CSRF middleware active
```

**Raw Structured Data** → triggers collapsible JSON card:
```json
{"key": "value"}
```

**CRITICAL**: Always use one of these formats. Never dump raw Python dict output or unformatted text. Do not include emojis.
"""
