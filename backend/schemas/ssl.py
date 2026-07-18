"""
schemas/ssl.py — Pydantic schemas for SSL cert operations.
"""
from pydantic import BaseModel, field_validator
from utils.validators import is_valid_domain


class CertIssueRequest(BaseModel):
    domain_id: int
    include_www: bool = False


class CertRenewRequest(BaseModel):
    cert_name: str

    @field_validator("cert_name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("cert_name cannot be empty")
        return v.strip()


class CertResponse(BaseModel):
    id: int
    domain_id: int
    full_domain: str
    cert_path: str | None
    expiry_date: str | None   # ISO string
    auto_renew: bool
    issued_at: str            # ISO string

    model_config = {"from_attributes": True}
