"""
schemas/proxy.py — Pydantic request/response schemas for reverse proxies.
"""
from datetime import datetime
from pydantic import BaseModel, field_validator
from utils.validators import (
    is_valid_subdomain_label,
    is_valid_domain,
    is_valid_ip,
    is_valid_port,
)


class ProxyCreate(BaseModel):
    """Managed-domain reverse proxy (panel DNS zone)."""
    domain_id: int
    subdomain: str
    target_ip: str
    target_port: int
    protocol: str = "http"
    enable_ssl: bool = False

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, v: str) -> str:
        v = v.strip().lower()
        if not is_valid_subdomain_label(v):
            raise ValueError(f"Invalid subdomain label: {v!r}")
        return v

    @field_validator("target_ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_ip(v):
            raise ValueError(f"Invalid IP address: {v!r}")
        return v

    @field_validator("target_port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not is_valid_port(v):
            raise ValueError(f"Port must be 1–65535, got {v}")
        return v

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("http", "https"):
            raise ValueError("Protocol must be http or https")
        return v


class ExternalProxyCreate(BaseModel):
    """External hostname reverse proxy (DNS outside the panel)."""
    hostname: str
    target_ip: str
    target_port: int
    protocol: str = "http"
    enable_ssl: bool = False

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str) -> str:
        v = v.strip().lower()
        if not is_valid_domain(v):
            raise ValueError(f"Invalid hostname: {v!r}")
        return v

    @field_validator("target_ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_ip(v):
            raise ValueError(f"Invalid IP address: {v!r}")
        return v

    @field_validator("target_port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not is_valid_port(v):
            raise ValueError(f"Port must be 1–65535, got {v}")
        return v

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("http", "https"):
            raise ValueError("Protocol must be http or https")
        return v


class ProxyResponse(BaseModel):
    id: int
    domain_id: int | None
    subdomain: str
    full_domain: str
    target_ip: str
    target_port: int
    protocol: str
    ssl_enabled: bool
    ssl_cert_id: int | None
    nginx_config_path: str | None
    dns_managed: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}
