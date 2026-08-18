"""
prompts/action_tags.py — Immutable action tag specifications for rich UI copy/apply badges.
"""
from __future__ import annotations

ACTION_TAGS_SPEC = """### Structured Action Tags:
When you suggest a concrete configuration parameter, port, or environment variable, include a structured action tag so the UI can provide an easy copy/apply button:
- For ports: `[ACTION:SET_PORT:<port_number>]` (e.g., `[ACTION:SET_PORT:3000]`)
- For environment variables: `[ACTION:SET_ENV:<KEY>=<VALUE>]` (e.g., `[ACTION:SET_ENV:NODE_ENV=production]`)
- For shell commands: `[ACTION:RUN_CMD:<command>]` (e.g., `[ACTION:RUN_CMD:npm install]`)
- For general suggestions: `[ACTION:SUGGESTION:<short_text>]`
- For secrets consent request: `[ACTION:ALLOW_SECRETS:session]` — renders as an amber "🔐 Unlock Credentials" button. Use ONLY when a file was blocked due to secrets policy and the user needs to consent to view it.
- For security audit findings: `[ACTION:SECURITY_FINDING:critical|warning|ok:<description>]` (e.g., `[ACTION:SECURITY_FINDING:critical:No rate limiting detected]`). Use inside or after a ```security block to render coloured severity badges.
"""
