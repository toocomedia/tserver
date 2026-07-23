"""Database-backed component state with a synchronous read cache."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import select

from database import AsyncSessionLocal
from models.component_state import ComponentState


@dataclass(frozen=True)
class ComponentStateValue:
    desired_enabled: bool = True
    operation: str = "idle"
    install_origin: str = "bundled"
    last_error: str | None = None
    updated_at: datetime | None = None


class ComponentStateStore:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], ComponentStateValue] = {}
        self._lock = asyncio.Lock()

    def get(
        self,
        component_type: str,
        component_id: str,
        *,
        default_enabled: bool = True,
    ) -> ComponentStateValue:
        return self._cache.get(
            (component_type, component_id),
            ComponentStateValue(desired_enabled=default_enabled),
        )

    async def initialize(
        self,
        components: Iterable[tuple[str, str, bool, str]],
    ) -> None:
        """Create missing state rows, then hydrate the complete cache."""
        async with self._lock:
            async with AsyncSessionLocal() as session:
                rows = (await session.execute(select(ComponentState))).scalars().all()
                existing = {(row.component_type, row.component_id): row for row in rows}

                for row in rows:
                    if row.operation != "idle":
                        row.operation = "idle"
                        row.last_error = "The panel restarted during this lifecycle operation."

                for component_type, component_id, enabled, origin in components:
                    key = (component_type, component_id)
                    if key not in existing:
                        row = ComponentState(
                            component_type=component_type,
                            component_id=component_id,
                            desired_enabled=enabled,
                            install_origin=origin,
                        )
                        session.add(row)
                        existing[key] = row

                await session.commit()
                rows = (await session.execute(select(ComponentState))).scalars().all()
                self._cache = {
                    (row.component_type, row.component_id): ComponentStateValue(
                        desired_enabled=row.desired_enabled,
                        operation=row.operation,
                        install_origin=row.install_origin,
                        last_error=row.last_error,
                        updated_at=row.updated_at,
                    )
                    for row in rows
                }

    async def set(
        self,
        component_type: str,
        component_id: str,
        *,
        desired_enabled: bool | None = None,
        operation: str | None = None,
        install_origin: str | None = None,
        last_error: str | None = None,
        clear_error: bool = False,
    ) -> ComponentStateValue:
        async with self._lock:
            async with AsyncSessionLocal() as session:
                row = await session.scalar(
                    select(ComponentState).where(
                        ComponentState.component_type == component_type,
                        ComponentState.component_id == component_id,
                    )
                )
                if row is None:
                    row = ComponentState(
                        component_type=component_type,
                        component_id=component_id,
                    )
                    session.add(row)

                if desired_enabled is not None:
                    row.desired_enabled = desired_enabled
                if operation is not None:
                    row.operation = operation
                if install_origin is not None:
                    row.install_origin = install_origin
                if clear_error:
                    row.last_error = None
                elif last_error is not None:
                    row.last_error = last_error[:1000]

                await session.commit()
                await session.refresh(row)
                value = ComponentStateValue(
                    desired_enabled=row.desired_enabled,
                    operation=row.operation,
                    install_origin=row.install_origin,
                    last_error=row.last_error,
                    updated_at=row.updated_at,
                )
                self._cache[(component_type, component_id)] = value
                return value


component_state_store = ComponentStateStore()
