"""
prompts/skills/error_diag.py — Error diagnosis task skill.
Injected when task_type="error_diag".
"""
from plugins.ai_helper.prompts.skills._base import SkillSpec

SKILL = SkillSpec(
    name="error_diag",
    task_types=["error_diag", "error", "debug"],
    prompt="""### Error Diagnosis Mode — Active:
You are diagnosing a deployment, runtime, or routing error on a VPS-hosted application.

**Required Tool Call Sequence**:
1. `get_app_logs` (app_id from context, or ask user) — Check recent deployment and runtime logs first.
2. `get_domains_and_ssl` — Verify domain SSL and nginx_active status.
3. `get_reverse_proxy_routes` — Check Nginx upstream is correctly pointing to the app port.
4. `get_apps_overview` — Confirm app status and host_port match the proxy upstream.

**Correlate**:
- If error is `Unexpected end of JSON input` on Node/Next.js/Umami:
  - Root cause: API routes crashed because database schema is not initialized or `APP_SECRET` is missing.
  - Fix: Ensure `APP_SECRET` is set in environment and start command runs database migration (`pnpm exec prisma db push && pnpm run start`).
- If `get_app_logs` shows missing database connection:
  - Note: Attached panel databases automatically inject `DATABASE_URL`. NEVER tell the user to manually copy/paste masked passwords `••••••••`.
- If logs show success but site returns 502 → proxy port mismatch (compare `host_port` vs proxy upstream).
- If logs show success and proxy is correct but returns 404 → webroot or routing issue.

**Output Format (Strict Rules: Keep under 8 lines, no emojis, exactly ONE action button)**:
```log
<1-3 critical log lines>
```
**Diagnosis**: <what failed in 1 sentence>
**Root Cause**: <why it failed in 1 sentence>
**Fix**: <exact correction in 1 sentence>

[ACTION:APP_REDEPLOY:<app_id>]
""",
)
