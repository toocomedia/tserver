"""
search_and_bulk.py — Reusable Backend Utilities for Search & Bulk Actions
Provides generic query filtering and bulk action handlers across SQLAlchemy models.
"""

from typing import List, Any, Type, Dict, Optional
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, String, cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import InstrumentedAttribute


class BulkActionRequest(BaseModel):
    """Generic payload schema for bulk endpoint requests."""
    action: str = Field(..., description="Bulk action name (e.g. 'delete', 'enable', 'disable', 'restart')")
    item_ids: List[int] = Field(default_factory=list, description="List of primary key IDs to operate on")
    ids: Optional[List[int]] = Field(default=None, description="Alias for item_ids")

    @model_validator(mode="before")
    @classmethod
    def normalize_ids(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "ids" in data and not data.get("item_ids"):
                data["item_ids"] = data["ids"]
            elif "item_ids" in data and not data.get("ids"):
                data["ids"] = data["item_ids"]
        return data

    @property
    def target_ids(self) -> List[int]:
        return self.item_ids or self.ids or []


def apply_search_filter(query: Any, model: Type[Any], search_term: str, search_fields: List[InstrumentedAttribute]) -> Any:
    """
    Applies a case-insensitive ILIKE search filter across specified model fields.
    
    :param query: SQLAlchemy select query
    :param model: SQLAlchemy model class
    :param search_term: Search string from request
    :param search_fields: List of model column attributes to search
    :return: Filtered SQLAlchemy query object
    """
    if not search_term or not search_term.strip() or not search_fields:
        return query

    term = f"%{search_term.strip()}%"
    conditions = []
    
    for field in search_fields:
        if hasattr(field, "ilike"):
            conditions.append(field.ilike(term))
        else:
            conditions.append(cast(field, String).ilike(term))
            
    return query.where(or_(*conditions))


async def execute_bulk_action(
    db: AsyncSession, 
    model: Type[Any], 
    action_type: str, 
    item_ids: List[int],
    status_column: str = "status"
) -> Dict[str, Any]:
    """
    Executes a generic bulk action (delete, enable, disable, start, stop) on a list of IDs.
    
    :param db: AsyncSession database session
    :param model: SQLAlchemy model class
    :param action_type: 'delete', 'enable', 'disable', 'start', 'stop'
    :param item_ids: List of integer IDs
    :param status_column: Name of status column if updating status
    :return: Dict containing count of processed items and status summary
    """
    if not item_ids:
        return {"success": False, "count": 0, "processed": 0, "failed": 0, "message": "No item IDs provided."}

    # Fetch items matching IDs
    stmt = select(model).where(model.id.in_(item_ids))
    result = await db.execute(stmt)
    items = result.scalars().all()

    if not items:
        return {"success": False, "count": 0, "processed": 0, "failed": 0, "message": "No matching items found."}

    count = 0
    action = action_type.lower().strip()

    if action == "delete":
        for item in items:
            await db.delete(item)
            count += 1
        await db.commit()
        return {"success": True, "count": count, "processed": count, "failed": 0, "message": f"Successfully deleted {count} item(s)."}

    elif action in ("enable", "active", "start"):
        for item in items:
            if hasattr(item, status_column):
                setattr(item, status_column, "active")
                count += 1
            elif hasattr(item, "enabled"):
                setattr(item, "enabled", True)
                count += 1
        await db.commit()
        return {"success": True, "count": count, "processed": count, "failed": 0, "message": f"Successfully enabled {count} item(s)."}

    elif action in ("disable", "inactive", "stop"):
        for item in items:
            if hasattr(item, status_column):
                setattr(item, status_column, "inactive")
                count += 1
            elif hasattr(item, "enabled"):
                setattr(item, "enabled", False)
                count += 1
        await db.commit()
        return {"success": True, "count": count, "processed": count, "failed": 0, "message": f"Successfully disabled {count} item(s)."}

    return {"success": False, "count": 0, "processed": 0, "failed": len(items), "message": f"Unsupported bulk action '{action_type}'."}
