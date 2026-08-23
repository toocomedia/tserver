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
- If error is `Container did not return a healthy HTTP response` / HTTP probe timeout:
  - Root cause: Health check path was set to `/health` or wrong endpoint on an app that uses `/api/health` (e.g. Plausible Analytics CE) or `/`, or the app is slow starting up due to DB migrations.
  - Fix: Propose patch for `health_path` (`/api/health` for Plausible, `/` for default web apps) and ensure `startup_timeout_seconds` is adequate (60s-120s).
- If `get_app_logs` shows missing database connection:
  - Note: Attached panel databases automatically inject `DATABASE_URL`. NEVER tell the user to manually copy/paste masked passwords `••••••••`.
**App Engine Draft Rule (CRITICAL)**:
- Whenever a configuration fix or setting modification is identified, you MUST ALWAYS execute the tool call `propose_container_app_patch` in your response (specifying app_id, patch dictionary e.g. {"health_path": "/api/health"}, and evidence).
- NEVER output raw text YAML blocks in the chat without calling `propose_container_app_patch`. The tool call is the only mechanism that creates the interactive "Apply changes" button for the user in the panel UI.

**Output Format (concise)**:
```log
<1-3 critical log lines>
```
**Diagnosis**: <what failed in 1 sentence>
**Root Cause**: <why it failed in 1 sentence>
**Action**: Staged draft fix via tool call. Click "Apply changes" in the Deployment changes card on the App page to apply and redeploy.
""",
)
