"""
service.py — AI Helper service facade re-exporting provider, session, resource, and chat services.
"""
from __future__ import annotations

from plugins.ai_helper.services.chat import stream_ai_chat
from plugins.ai_helper.services.providers import (
    PROVIDER_PRESETS,
    create_provider,
    decrypt_key,
    delete_provider,
    encrypt_key,
    fetch_models,
    get_active_provider,
    get_provider,
    list_providers,
    set_default_provider,
    test_connection,
    test_provider,
    update_provider,
)
from plugins.ai_helper.services.resources import (
    get_audit_logs,
    get_discoverable_resources,
    get_permission_policy,
    update_permission_policy,
)
from plugins.ai_helper.services.sessions import (
    clear_all_sessions,
    clear_session,
    delete_session,
    generate_title_from_prompt,
    get_or_create_session,
    get_session,
    get_session_messages,
    list_sessions,
    update_session,
)

# Alias for backwards compatibility
_generate_title_from_prompt = generate_title_from_prompt


class AiHelperService:
    """Plugin lifecycle hooks for the native AI Assistant plugin."""

    def is_installed(self) -> bool:
        return True

    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None


ai_helper_service = AiHelperService()

__all__ = [
    "PROVIDER_PRESETS",
    "encrypt_key",
    "decrypt_key",
    "list_providers",
    "get_provider",
    "get_active_provider",
    "create_provider",
    "update_provider",
    "delete_provider",
    "set_default_provider",
    "test_provider",
    "test_connection",
    "fetch_models",
    "get_permission_policy",
    "update_permission_policy",
    "get_audit_logs",
    "get_discoverable_resources",
    "generate_title_from_prompt",
    "_generate_title_from_prompt",
    "get_or_create_session",
    "list_sessions",
    "get_session",
    "update_session",
    "delete_session",
    "clear_all_sessions",
    "get_session_messages",
    "clear_session",
    "stream_ai_chat",
    "ai_helper_service",
    "AiHelperService",
]
