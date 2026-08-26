"""
schemas.py — Pydantic request and response schemas for AI Helper plugin.
"""
from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field


class ProviderPayload(BaseModel):
    name: str = Field(..., min_length=1)
    provider_type: str = "openai_compatible"
    api_key: Optional[str] = None
    base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4o-mini"
    models: Optional[List[str]] = None
    models_list: Optional[str] = None
    temperature: Optional[float] = 0.1
    max_tokens: Optional[int] = 4096
    custom_rules: Optional[str] = ""
    is_default: Optional[bool] = False
    is_enabled: Optional[bool] = True


class TestConnectionRequest(BaseModel):
    provider_type: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: Optional[str] = None
    provider_id: Optional[int] = None


class FetchModelsRequest(BaseModel):
    provider_type: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    provider_id: Optional[int] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    task_type: Optional[str] = "general"
    session_title: Optional[str] = None
    provider_id: Optional[int] = None
    model_name: Optional[str] = None
    context_key: Optional[str] = None
    context: Optional[str] = None
    stream: bool = True
    allow_secrets: bool = False  # user explicitly grants secrets consent for this message/session


class CreateSessionPayload(BaseModel):
    title: Optional[str] = "New Chat"
    task_type: Optional[str] = "general"
    context_key: Optional[str] = None
    provider_id: Optional[int] = None
    model_name: Optional[str] = None


class UpdateSessionPayload(BaseModel):
    title: Optional[str] = None
    task_type: Optional[str] = None
    is_archived: Optional[bool] = None


class ProviderSummary(BaseModel):
    id: int
    name: str
    provider_type: str
    model_name: str
    models: List[str] = []
    is_default: bool
    is_enabled: bool


class PermissionPolicyPayload(BaseModel):
    global_mode: Optional[str] = "full_read_only"  # full_read_only | selective | disabled
    allow_domains_proxy: Optional[bool] = True
    allow_dns: Optional[bool] = True
    allow_php_sites: Optional[bool] = True
    allow_container_apps: Optional[bool] = True
    allow_databases: Optional[bool] = True
    allow_files_read: Optional[bool] = True
    allow_web_fetch: Optional[bool] = False
    allow_file_edits: Optional[bool] = False
    allow_app_deploy: Optional[bool] = False
    allowed_domains: Optional[Any] = "[]"
    allowed_app_ids: Optional[Any] = "[]"
    allowed_databases: Optional[Any] = "[]"
    allowed_file_targets: Optional[Any] = "[]"
    ask_on_demand: Optional[bool] = False

