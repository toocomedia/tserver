"""
utils/pagination.py — Reusable base pagination helper for SQLAlchemy queries.
"""
from typing import Any, Sequence
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import config


async def paginate_query(
    db: AsyncSession,
    stmt: Any,
    offset: int = 0,
    limit: int = config.DEFAULT_PAGE_LIMIT
) -> tuple[Sequence[Any], int]:
    """
    Applies OFFSET and LIMIT pagination to any SQLAlchemy SELECT statement.
    Returns (items, total_count).
    """
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one_or_none() or 0

    paginated_stmt = stmt.offset(offset).limit(limit)
    res = await db.execute(paginated_stmt)
    items = res.scalars().all()
    return items, total
