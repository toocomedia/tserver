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
- If `get_app_logs` shows a crash → root cause is in the app itself (check startup error).
- If logs show success but site returns 502 → proxy port mismatch (compare `host_port` vs proxy upstream).
- If logs show success and proxy is correct but returns 404 → webroot or routing issue.
- If `nginx_active: false` → Nginx config for this domain may not be active.

**Output Format**:
```log
<relevant log lines here>
```
Then: **Diagnosis** (what failed), **Root Cause** (why), **Fix** (exact steps with action tags).
""",
)
