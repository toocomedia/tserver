"""
schemas.py — Pydantic request and response schemas for AI Helper plugin.
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class ProviderPayload(BaseModel):
    name: str = Field(..., min_length=1)
    provider_type: str = "openai_compatible"
    api_key: Optional[str] = None
    base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4o-mini"
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 4096
    custom_rules: Optional[str] = ""
    is_default: Optional[bool] = False
    is_enabled: Optional[bool] = True


class TestConnectionRequest(BaseModel):
    provider_type: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: Optional[str] = None


class FetchModelsRequest(BaseModel):
    provider_type: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    provider_id: Optional[int] = None
    model_name: Optional[str] = None
    context_key: Optional[str] = None
    context: Optional[str] = None
    stream: bool = True


class ProviderSummary(BaseModel):
    id: int
    name: str
    provider_type: str
    model_name: str
    is_default: bool
    is_enabled: bool
