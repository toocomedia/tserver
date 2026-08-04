"""Persistent operation records used by the Resource Guard queue."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.guard_operation import GuardOperation
from services.resource_guard_profiles import PROFILES

_ACTIVE = ("running", "cancelling")
_FINAL = {"succeeded", "failed", "cancelled", "interrupted", "blocked"}


class ResourceGuardOperationService:
    def __init__(self) -> None:
        self._cancel_callbacks: dict[int, Callable[[], None]] = {}

    async def create(
        self, db: AsyncSession, *, component_type: str, component_id: str,
        operation_type: str, priority: str, label: str, profile: str,
        status: str, deployment_id: int | None = None,
        cancel: Callable[[], None] | None = None,
    ) -> GuardOperation:
        operation = GuardOperation(
            component_type=component_type, component_id=component_id,
            operation_type=operation_type, priority=priority, label=label,
            profile=profile, reserved_mb=PROFILES[profile]["ram_mb"],
            status=status, deployment_id=deployment_id,
            started_at=datetime.utcnow() if status == "running" else None,
        )
        db.add(operation)
        await db.flush()
        if cancel:
            self._cancel_callbacks[operation.id] = cancel
        await self._renumber_queue(db)
        return operation

    async def finish(self, db: AsyncSession, operation_id: int, status: str) -> None:
        operation = await db.get(GuardOperation, operation_id)
        if operation is None or operation.status in _FINAL:
            return
        operation.status, operation.finished_at = status, datetime.utcnow()
        operation.queue_position = None
        self._cancel_callbacks.pop(operation.id, None)
        await self._renumber_queue(db)

    async def cancel(self, db: AsyncSession, operation_id: int) -> GuardOperation | None:
        operation = await db.get(GuardOperation, operation_id)
        if operation is None or operation.status in _FINAL:
            return operation
        if operation.status == "queued":
            operation.status, operation.finished_at = "cancelled", datetime.utcnow()
            operation.queue_position = None
        else:
            operation.status, operation.cancel_reason = "cancelling", "Stopped by the user."
        callback = self._cancel_callbacks.get(operation.id)
        if callback:
            callback()
        await self._renumber_queue(db)
        return operation

    async def list(self, db: AsyncSession, limit: int = 100) -> list[GuardOperation]:
        return list((await db.scalars(select(GuardOperation).order_by(
            GuardOperation.created_at.desc(), GuardOperation.id.desc()
        ).limit(limit))).all())

    async def next_queued(self, db: AsyncSession) -> GuardOperation | None:
        return await db.scalar(select(GuardOperation).where(
            GuardOperation.status == "queued"
        ).order_by(GuardOperation.created_at, GuardOperation.id))

    async def start(self, db: AsyncSession, operation: GuardOperation) -> None:
        operation.status, operation.started_at, operation.queue_position = "running", datetime.utcnow(), None

    async def recover(self, db: AsyncSession) -> None:
        rows = list((await db.scalars(select(GuardOperation).where(
            GuardOperation.status.in_(_ACTIVE)
        ))).all())
        for operation in rows:
            operation.status, operation.finished_at = "interrupted", datetime.utcnow()
        await self._renumber_queue(db)

    async def record_sample(self, db: AsyncSession, total_mb: int, available_mb: int) -> None:
        used = max(0, total_mb - available_mb)
        rows = (await db.scalars(select(GuardOperation).where(
            GuardOperation.status.in_(_ACTIVE)
        ))).all()
        for operation in rows:
            operation.current_ram_mb = used
            operation.peak_ram_mb = max(operation.peak_ram_mb or 0, used)

    async def _renumber_queue(self, db: AsyncSession) -> None:
        rows = (await db.scalars(select(GuardOperation).where(
            GuardOperation.status == "queued"
        ).order_by(GuardOperation.created_at, GuardOperation.id))).all()
        for index, operation in enumerate(rows, 1):
            operation.queue_position = index

    @staticmethod
    def data(operation: GuardOperation) -> dict:
        return {field: getattr(operation, field) for field in (
            "id", "component_type", "component_id", "operation_type", "label",
            "profile", "reserved_mb", "priority", "status", "queue_position",
            "current_ram_mb", "peak_ram_mb", "current_cpu", "deployment_id",
            "started_at", "finished_at", "created_at",
        )}


resource_guard_operation_service = ResourceGuardOperationService()
