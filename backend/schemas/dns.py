"""
schemas/dns.py — Pydantic schemas for DNS records and template application.
"""
from pydantic import BaseModel, field_validator

VALID_RECORD_TYPES = {"A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA"}


class RecordCreate(BaseModel):
    name: str
    type: str
    content: str
    ttl: int = 3600

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Record name cannot be empty")
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in VALID_RECORD_TYPES:
            raise ValueError(f"Invalid record type: {v}. Allowed: {', '.join(sorted(VALID_RECORD_TYPES))}")
        return v

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Record content cannot be empty")
        return v

    @field_validator("ttl")
    @classmethod
    def validate_ttl(cls, v: int) -> int:
        if v < 60 or v > 86400:
            raise ValueError("TTL must be between 60 and 86400 seconds")
        return v


class TemplateApply(BaseModel):
    template_name: str

    @field_validator("template_name")
    @classmethod
    def validate_template(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Template name cannot be empty")
        return v


class RecordDelete(BaseModel):
    name: str
    type: str
