"""
schemas.py — Pydantic request / response models for the PostgreSQL Manager plugin.
"""
from pydantic import BaseModel, Field, field_validator


# ------------------------------------------------------------------
# Databases
# ------------------------------------------------------------------

class DatabaseCreate(BaseModel):
    name: str
    owner: str = "postgres"

    @field_validator("name", "owner")
    @classmethod
    def must_be_safe_ident(cls, v: str) -> str:
        import re
        if not re.match(r"^[a-zA-Z0-9_\-]{1,63}$", v):
            raise ValueError("Invalid identifier — use letters, digits, _ or - only.")
        return v


class DatabaseResponse(BaseModel):
    name: str
    owner: str
    encoding: str
    size: str


# ------------------------------------------------------------------
# Users
# ------------------------------------------------------------------

class UserCreate(BaseModel):
    name: str
    password: str

    @field_validator("name")
    @classmethod
    def must_be_safe_ident(cls, v: str) -> str:
        import re
        if not re.match(r"^[a-zA-Z0-9_\-]{1,63}$", v):
            raise ValueError("Invalid username — use letters, digits, _ or - only.")
        return v

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return v


class PasswordChange(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return v


class UserResponse(BaseModel):
    name: str
    superuser: bool
    can_login: bool


# ------------------------------------------------------------------
# Query runner
# ------------------------------------------------------------------

class QueryRequest(BaseModel):
    db: str
    sql: str

    @field_validator("db")
    @classmethod
    def must_be_safe_ident(cls, v: str) -> str:
        import re
        if not re.match(r"^[a-zA-Z0-9_\-]{1,63}$", v):
            raise ValueError("Invalid database name.")
        return v

    @field_validator("sql")
    @classmethod
    def must_be_select(cls, v: str) -> str:
        import re
        if not re.match(r"^\s*SELECT\b", v, re.IGNORECASE):
            raise ValueError("Only SELECT statements are allowed.")
        return v


class QueryResponse(BaseModel):
    rows: list[dict]
    count: int


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------

class StatusResponse(BaseModel):
    installed: bool
    running: bool
    version: str
    pid: int | None
    ram_mb: float
    port_open: bool
    mode: str


# ------------------------------------------------------------------
# Service action
# ------------------------------------------------------------------

class ServiceActionResponse(BaseModel):
    status: str
    action: str


# ------------------------------------------------------------------
# Remote Access & SSL
# ------------------------------------------------------------------

class RemoteConfigRequest(BaseModel):
    mode: str = "managed"  # "managed" or "external"
    domain: str | None = None
    subdomain: str | None = None
    hostname: str | None = None
    issue_ssl: bool = True
    allowed_cidrs: list[str] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def mode_must_be_valid(cls, v: str) -> str:
        if v not in ("managed", "external"):
            raise ValueError("Mode must be 'managed' or 'external'.")
        return v

    @field_validator("allowed_cidrs")
    @classmethod
    def cidrs_must_be_valid(cls, values: list[str]) -> list[str]:
        import ipaddress
        normalized: list[str] = []
        for value in values:
            try:
                network = ipaddress.ip_network(value.strip(), strict=False)
            except ValueError as exc:
                raise ValueError(f"Invalid client IP/CIDR: {value}") from exc
            rendered = str(network)
            if rendered not in normalized:
                normalized.append(rendered)
        return normalized


class RemoteStatusResponse(BaseModel):
    enabled: bool
    domain: str | None
    ssl_active: bool
    nginx_stream: bool


class RemoteDomainResponse(BaseModel):
    domain: str
    mode: str
    ssl_active: bool
    nginx_stream: bool
