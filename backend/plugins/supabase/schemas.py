"""Pydantic schemas for the Supabase plugin."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator, model_validator


# ──────────────────────────────────────────────
# Projects
# ──────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    db_host: str
    db_port: int = 5432
    db_name: str = "postgres"
    db_user: str = "postgres"
    db_password: str
    pat: str | None = None
    region: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name is required.")
        return v

    @field_validator("db_host")
    @classmethod
    def host_valid(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v:
            raise ValueError("DB host is required.")
        return v

    @field_validator("db_port")
    @classmethod
    def port_range(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("Port must be 1–65535.")
        return v

    @field_validator("db_password")
    @classmethod
    def password_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("DB password is required.")
        return v


class ProjectUpdate(BaseModel):
    name: str | None = None
    db_password: str | None = None
    pat: str | None = None
    region: str | None = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    project_ref: str | None
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    region: str | None
    connection_status: str
    last_connected_at: datetime | None
    created_at: datetime


class ProjectListItem(BaseModel):
    id: int
    name: str
    db_host: str
    region: str | None
    connection_status: str
    last_connected_at: datetime | None


# ──────────────────────────────────────────────
# Query runner
# ──────────────────────────────────────────────

class QueryRequest(BaseModel):
    project_id: int
    db: str
    sql: str

    @field_validator("db")
    @classmethod
    def safe_db(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_\-]{1,63}$", v):
            raise ValueError("Invalid database name.")
        return v

    @field_validator("sql")
    @classmethod
    def select_only(cls, v: str) -> str:
        if not re.match(r"^\s*SELECT\b", v, re.IGNORECASE):
            raise ValueError("Only SELECT statements allowed.")
        return v


class QueryResponse(BaseModel):
    rows: list[dict[str, Any]]
    count: int


# ──────────────────────────────────────────────
# App provisioning (used by container_app_database_service)
# ──────────────────────────────────────────────

class ProvisionRequest(BaseModel):
    project_id: int
    database_name: str
    username: str
    password: str

    @field_validator("database_name", "username")
    @classmethod
    def safe_ident(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_]{1,63}$", v):
            raise ValueError(f"Invalid identifier: {v!r}")
        return v
