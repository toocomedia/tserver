"""
prompts/action_tags.py — Immutable action tag specifications for rich UI copy/apply badges.
"""
from __future__ import annotations

ACTION_TAGS_SPEC = """### Structured Action Tags:
When you suggest a concrete configuration parameter, port, or environment variable, include a structured action tag so the UI can provide an easy copy/apply button:
- Never emit App Engine deploy, redeploy, apply, retry, start, rollback, or credential action tags. Those controls exist only on the App page after server validation.
- For file modification plans: `[ACTION:FILE_PLAN:<plan_id>]` — renders an interactive "Review File Changes" card. Always obtain `plan_id` from `propose_file_edit`.
- For ports: `[ACTION:SET_PORT:<port_number>]` (e.g., `[ACTION:SET_PORT:3000]`)
- For environment variables: `[ACTION:SET_ENV:<KEY>=<VALUE>]` (e.g., `[ACTION:SET_ENV:NODE_ENV=production]`)
- For shell commands: `[ACTION:RUN_CMD:<command>]` (e.g., `[ACTION:RUN_CMD:npm install]`)
- For general suggestions: `[ACTION:SUGGESTION:<short_text>]`
- Never emit a credential-unlock action tag. The server alone renders one after `read_website_file` returns `status=secrets_blocked` for a user-requested sensitive file.
- For security audit findings: `[ACTION:SECURITY_FINDING:critical|warning|ok:<description>]` (e.g., `[ACTION:SECURITY_FINDING:critical:No rate limiting detected]`). Use inside or after a ```security block to render coloured severity badges.
"""
