"""
schemas/error.py — Pydantic schemas for admin error events.
"""
from datetime import datetime
from pydantic import BaseModel


class ErrorEventResponse(BaseModel):
    id: int
    created_at: datetime
    level: str
    source: str
    operation: str
    message: str
    detail: str | None
    traceback: str | None
    request_method: str | None
    request_path: str | None
    request_id: str | None
    context_json: str | None
    resolved: bool
    resolved_at: datetime | None

    model_config = {"from_attributes": True}
