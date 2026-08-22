"""
prompts/action_tags.py — Immutable action tag specifications for rich UI copy/apply badges.
"""
from __future__ import annotations

ACTION_TAGS_SPEC = """### Structured Action Tags:
When you suggest a concrete configuration parameter, port, or environment variable, include a structured action tag so the UI can provide an easy copy/apply button:
- For application setup plans: `[ACTION:APP_PLAN:<plan_id>]` — renders an interactive "Apply to Deploy Form" card in chat. Always obtain `plan_id` from the `propose_app_install` tool. NEVER write raw JSON.
- For application deployment: `[ACTION:APP_DEPLOY]` — renders an interactive "Accept & Deploy Application" button in chat.
- For application redeployment & rebuild: `[ACTION:APP_REDEPLOY:<app_id>]` — renders an interactive "Redeploy Application Now" card in chat to rebuild and restart an existing container app.
- For file modification plans: `[ACTION:FILE_PLAN:<plan_id>]` — renders an interactive "Review File Changes" card. Always obtain `plan_id` from `propose_file_edit`.
- For ports: `[ACTION:SET_PORT:<port_number>]` (e.g., `[ACTION:SET_PORT:3000]`)
- For environment variables: `[ACTION:SET_ENV:<KEY>=<VALUE>]` (e.g., `[ACTION:SET_ENV:NODE_ENV=production]`)
- For shell commands: `[ACTION:RUN_CMD:<command>]` (e.g., `[ACTION:RUN_CMD:npm install]`)
- For general suggestions: `[ACTION:SUGGESTION:<short_text>]`
- For secrets consent request: `[ACTION:ALLOW_SECRETS:session]` — renders as an interactive Unlock Credentials button in the chat. Use whenever credentials, passwords, or sensitive config files are requested or masked.
- For security audit findings: `[ACTION:SECURITY_FINDING:critical|warning|ok:<description>]` (e.g., `[ACTION:SECURITY_FINDING:critical:No rate limiting detected]`). Use inside or after a ```security block to render coloured severity badges.
"""

