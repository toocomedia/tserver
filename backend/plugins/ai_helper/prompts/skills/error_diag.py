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
1. `get_app_engine_diagnostics` for an App Engine app; otherwise use the relevant logs/status tools. Do NOT fetch external documentation or scan unrelated directories.
2. Inspect container stdout/stderr logs and health states.

**Correlate**:
- If container image failed to pull (wrong image reference, missing tag, or typo):
  - Root cause: Image reference does not exist or requires authentication.
  - Fix: Stage the corrected image in `patch` via `propose_container_app_patch(app_id=..., patch={"image_reference": "<correct_image>"}, evidence=["Correct public official image"])`.
- If error is 502 Bad Gateway or container process exited on startup:
  - Root cause: Web container process crashed due to invalid/missing environment variables (e.g. `BASE_URL`), invalid framework secrets (e.g. `SECRET_KEY_BASE` uses `base64_48`, `TOTP_VAULT_KEY` strictly requires `base64_32` for 32 bytes, `APP_KEY`), or memory limits.
  - Fix: Stage the exact fix via `propose_container_app_patch(app_id=..., patch={}, environment_values={...}, secret_requirements=[{"key": "TOTP_VAULT_KEY", "purpose": "TOTP encryption key", "generator": "base64_32", "rotate": True}], evidence=[...])`. Do NOT recommend deleting or reinstalling the entire app.
- If an HTTP probe fails while the process is running:
  - Root cause: the verified path may be wrong or temporarily unavailable; state is degraded, not automatically failed.
  - Fix: only propose a new `health_path` when exact source/vendor evidence supports it.
- If `get_app_logs` shows missing database connection:
  - Note: Attached panel databases automatically inject `DATABASE_URL`. NEVER tell the user to manually copy/paste masked passwords `••••••••`.

**App Engine Draft Rule (MANDATORY & CRITICAL)**:
- Whenever ANY image reference, environment variable (e.g. `KAFKA_BROKERS`, `DATABASE_URL`, `CLICKHOUSE_DB`, `CLICKHOUSE_URL`, `BASE_URL`), secret, or configuration fix is identified on ANY single-app or multi-container official stack, YOU MUST EXECUTE `propose_container_app_patch(app_id=..., patch=..., environment_values=..., secret_requirements=..., evidence=...)` (use safe uppercase keys and single-line values for environment_values).
- NEVER output plain-text instructions or manual configuration recommendations in your response without invoking `propose_container_app_patch`. Executing the tool creates the draft action plan and automatically attaches the interactive "Apply Fix & Redeploy" button to your message.


**Output Format**:
```log
<1-3 critical log lines from deployment or container stderr>
```
**Diagnosis**: <what failed in 1 sentence>
**Root Cause**: <why it failed in 1 sentence>
**Files & Configuration Being Edited**:
- List the specific environment variables, configuration parameters, or service `.env` / compose settings being updated.
**Container Lifecycle**:
- Explain that upon clicking Apply Fix & Redeploy, existing containers are safely stopped and recreated with `--force-recreate` using the new snapshot, cleanly superseding the failed deployment without deleting persistent storage volumes.
**Action**: Staged fix via AI proposal. Click **Apply Fix & Redeploy** below to apply changes and redeploy immediately.
""",
)
