"""
plugins/external_dns/schemas.py — Request bodies for the External DNS API.

Credentials are provider-specific, so they arrive as a free-form string map and
are filtered down to the provider's declared fields by the service. This keeps
the API generic — a new provider needs no schema change.
"""
from pydantic import BaseModel, Field


class TestRequest(BaseModel):
    provider: str
    credentials: dict[str, str] = Field(default_factory=dict)
    zone_ref: str = ""


class BindRequest(BaseModel):
    domain: str
    provider: str
    credentials: dict[str, str] = Field(default_factory=dict)
    zone_ref: str = ""


class UnbindRequest(BaseModel):
    domain: str
