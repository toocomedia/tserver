"""
schemas.py — Pydantic request / response models for the PostgreSQL Manager plugin.
"""
from typing import Literal
import ipaddress
from pydantic import BaseModel, field_validator, model_validator


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


class RemoteConfigRequest(BaseModel):
    mode: Literal["managed", "external"]
    domain: str | None = None
    subdomain: str | None = None
    hostname: str | None = None
    encryption_enabled: bool = True
    # Compatibility with the existing UI payload while it migrates.
    issue_ssl: bool | None = None
    allowed_cidrs: list[str] = ["0.0.0.0/0"]

    @model_validator(mode="after")
    def valid_mode_fields(self):
        if self.mode == "managed" and not (self.domain and self.subdomain):
            raise ValueError("Managed mode requires a domain and subdomain.")
        if self.mode == "external" and not self.hostname:
            raise ValueError("External mode requires a hostname.")
        if self.issue_ssl is not None:
            self.encryption_enabled = self.issue_ssl
        return self

    @field_validator("allowed_cidrs")
    @classmethod
    def valid_cidrs(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("Add at least one allowed IP range.")
        for value in values:
            ipaddress.ip_network(value, strict=False)
        return values
