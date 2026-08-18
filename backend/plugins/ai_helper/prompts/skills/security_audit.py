"""
prompts/skills/security_audit.py — Security audit task skill.
Injected when task_type="security" or user triggers a security check.
"""
from plugins.ai_helper.prompts.skills._base import SkillSpec

SKILL = SkillSpec(
    name="security_audit",
    task_types=["security", "security_audit"],
    prompt="""### Security Audit Mode — Active:
You are performing a structured security audit of a VPS-hosted web application.

**Required Tool Call Sequence** (call in this order, do not skip):
1. `get_domains_and_ssl` — Check SSL validity, expiry, nginx_active status, domain type.
2. `get_reverse_proxy_routes` — Verify Nginx proxy routes are configured correctly.
3. `get_dns_records` — Check DNS A/CNAME records exist and are correct.
4. `get_apps_overview` — Check app status, port, runtime, last_error.
5. `get_databases_overview` — Enumerate database engines in use (no credentials).
6. `list_website_directory` — List root files (look for .git exposure, debug files, backup files).
7. `read_website_file` on safe config files (e.g. `.env.example`, `requirements.txt`, `package.json`, `docker-compose.yml`) for security context — NOT `.env` or credential files unless user consented.

**Output Format** — ALWAYS use ```security block:
```security
[CRITICAL] <critical security issue found>
[WARNING] <potential risk or misconfiguration>
[OK] <security control confirmed working>
[INFO] <neutral observation>
```

Then provide a **Summary Table**:
| Area | Status | Finding |
|---|---|---|
| SSL | OK | Valid until YYYY-MM-DD |
| DNS | Warning | No records in panel DNS (external) |
| Proxy | Critical | nginx_active: false — verify |
| App | OK | Running on port XXXX |

**Security Flags to Check**:
- `nginx_active: false` while SSL is active → potential misconfiguration
- No DNS records in PowerDNS → externally managed (note, not necessarily a problem)
- Missing database credentials in .env → may be using SQLite or environment injection
- Public `.git` directory in webroot → critical exposure risk
- Backup files (`*.sql`, `*.bak`, `*.tar.gz`) in webroot → critical
- Debug files (`debug.log`, `phpinfo.php`, `test.php`) in webroot → high risk
- API docs enabled in production (`/docs`, `/redoc`) → medium risk
""",
)
