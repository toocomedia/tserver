"""
models/ai_helper.py — ORM models for AI Helper configuration, provider catalog, and multi-turn chat history.
"""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class AiProvider(Base):
    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), default="openai_compatible", nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str] = mapped_column(String(512), default="https://api.openai.com/v1", nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini", nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    custom_rules: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    models_list: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_tested_status: Mapped[str] = mapped_column(String(32), default="untested", nullable=False)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def get_models(self) -> list[str]:
        """Returns list of enabled model names for this provider."""
        models: list[str] = []
        if self.models_list and self.models_list.strip():
            raw = self.models_list.strip()
            if raw.startswith("["):
                try:
                    import json
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        models = [str(m).strip() for m in parsed if str(m).strip()]
                except Exception:
                    pass
            if not models:
                models = [m.strip() for m in raw.split(",") if m.strip()]

        if self.model_name and self.model_name.strip() and self.model_name.strip() not in models:
            models.insert(0, self.model_name.strip())

        if not models:
            models = [self.model_name.strip() if self.model_name else "gpt-4o-mini"]

        return models


class AiHelperSettings(Base):
    __tablename__ = "ai_helper_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), default="openai_compatible", nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str] = mapped_column(String(512), default="https://api.openai.com/v1", nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini", nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    custom_rules: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AiChatSession(Base):
    __tablename__ = "ai_chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="New Chat", nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    context_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AiChatMessage(Base):
    __tablename__ = "ai_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "user" | "assistant" | "system"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    context_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class AiPermissionPolicy(Base):
    __tablename__ = "ai_permission_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    global_mode: Mapped[str] = mapped_column(String(32), default="full_read_only", nullable=False)  # full_read_only | selective | disabled
    allow_domains_proxy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_dns: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_php_sites: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_container_apps: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_databases: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_files_read: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_web_fetch: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_file_edits: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_app_deploy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allowed_domains: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    allowed_app_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    allowed_databases: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    allowed_file_targets: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    ask_on_demand: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AiActionPlan(Base):
    """Immutable server-persisted action plan proposed by AI Assistant."""
    __tablename__ = "ai_action_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "app_install" | "file_edit"
    status: Mapped[str] = mapped_column(String(32), default="awaiting_approval", nullable=False)  # "draft" | "awaiting_input" | "awaiting_approval" | "applied" | "expired" | "cancelled"
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 of payload_json
    summary: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AiActionEvent(Base):
    """Audit history and state transitions for action plans."""
    __tablename__ = "ai_action_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "created" | "viewed" | "applied" | "rejected" | "expired"
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


