"""
services/secrets_consent.py — In-memory per-session secrets consent registry.

A session starts with secrets BLOCKED. The user must explicitly type a consent phrase
(e.g. "show me the actual credentials", "I allow secrets", "/allow secrets") in the
chat to enable secret visibility for that session only. Resets on server restart.
"""
from __future__ import annotations

import re
from typing import Set

# -------------------------------------------------------------------------
# Consent Store — ephemeral, intentionally not persisted to DB.
# Losing state on restart is a security feature, not a bug.
# -------------------------------------------------------------------------
_CONSENTED_SESSIONS: Set[str] = set()

# Phrases that the user can type in chat to grant consent.
_CONSENT_PATTERNS = re.compile(
    r"""
    (?:
        /allow\s+secrets?        |   # slash command: /allow secrets
        show\s+(?:me\s+)?(?:the\s+)?(?:actual\s+)?(?:real\s+)?
            (?:credentials?|passwords?|secrets?|api\s*keys?|keys?)  |
        i\s+(?:allow|permit|authorize|grant)\s+(?:secrets?|credentials?|passwords?|keys?)  |
        reveal\s+(?:the\s+)?(?:secrets?|credentials?|passwords?|api\s*keys?|keys?)  |
        (?:un)?mask\s+(?:the\s+)?(?:secrets?|credentials?|passwords?|api\s*keys?)  |
        display\s+(?:the\s+)?(?:full\s+)?(?:credentials?|passwords?|secrets?)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Phrases to explicitly revoke consent mid-session.
_REVOKE_PATTERNS = re.compile(
    r"""
    (?:
        /revoke\s+secrets?      |
        /block\s+secrets?       |
        stop\s+showing\s+(?:secrets?|credentials?|passwords?)  |
        hide\s+(?:the\s+)?(?:secrets?|credentials?|passwords?)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def check_consent_phrase(session_id: str, user_message: str) -> bool:
    """
    Scans the user message for explicit consent or revocation phrases.
    Updates the consent store accordingly.
    Returns True if consent was newly granted this turn.
    """
    if _REVOKE_PATTERNS.search(user_message):
        _CONSENTED_SESSIONS.discard(session_id)
        return False

    if _CONSENT_PATTERNS.search(user_message):
        _CONSENTED_SESSIONS.add(session_id)
        return True

    return False


def is_secrets_allowed(session_id: str) -> bool:
    """Returns True if the user has explicitly granted secrets consent for this session."""
    return session_id in _CONSENTED_SESSIONS


def revoke_consent(session_id: str) -> None:
    """Explicitly revokes secrets consent for a session."""
    _CONSENTED_SESSIONS.discard(session_id)


def grant_consent(session_id: str) -> None:
    """Explicitly grants secrets consent for a session (e.g. via API endpoint)."""
    _CONSENTED_SESSIONS.add(session_id)
