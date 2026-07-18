"""
schemas/domain.py — Pydantic request/response schemas for Domain.
"""
from datetime import datetime
from pydantic import BaseModel, field_validator
from utils.validators import is_valid_domain


class DomainCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not is_valid_domain(v):
            raise ValueError(f"Invalid domain name: {v!r}")
        return v


class DomainResponse(BaseModel):
    id: int
    name: str
    server_ip: str
    nginx_config_path: str | None
    webroot_path: str | None
    dns_zone_created: bool
    nginx_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PageEditRequest(BaseModel):
    content: str

    @field_validator("content")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Page content cannot be empty")
        return v
