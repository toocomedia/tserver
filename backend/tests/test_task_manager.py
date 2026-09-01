"""Tests for Unified TaskManagerService and Task Manager APIs."""
import asyncio
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from starlette.middleware.sessions import SessionMiddleware
from routers.tasks import router as tasks_router
from services.task_manager_service import TaskManagerService


@pytest.mark.asyncio
async def test_task_manager_service_lifecycle():
    service = TaskManagerService()
    
    # 1. Active locks initial state
    locks = service.get_active_locks()
    assert locks["active_count"] == 0
    assert locks["has_running"] is False
    assert locks["apt_locked"] is False

    # 2. Spawn a successful background task
    async def sample_runner(rec):
        rec.add_log("Step 1 done")
        await asyncio.sleep(0.05)
        rec.add_log("Step 2 done")
        return True, "Done successfully"

    task = await service.spawn(
        category="plugin",
        action="install",
        target_id="test_plugin",
        label="Install Test Plugin",
        runner=sample_runner,
        lock_type="apt",
    )

    assert task.status == "running"
    assert task.lock_type == "apt"

    locks_during = service.get_active_locks()
    assert locks_during["active_count"] == 1
    assert locks_during["apt_locked"] is True

    # Wait for completion
    await asyncio.sleep(0.15)

    assert task.status == "succeeded"
    assert len(task.logs) >= 2
    assert "Done successfully" in task.logs[-1]

    locks_after = service.get_active_locks()
    assert locks_after["active_count"] == 0
    assert locks_after["apt_locked"] is False
    assert len(service.list_history()) == 1


@pytest.mark.asyncio
async def test_task_manager_cancellation():
    service = TaskManagerService()
    cancelled_flag = False

    def on_cancel():
        nonlocal cancelled_flag
        cancelled_flag = True

    async def long_runner(rec):
        await asyncio.sleep(2.0)
        return True, "Should not reach here"

    task = await service.spawn(
        category="dependency",
        action="install",
        target_id="mariadb",
        label="Install MariaDB",
        runner=long_runner,
        cancel_callback=on_cancel,
    )

    assert task.status == "running"
    ok = await service.cancel(task.id)
    assert ok is True
    assert task.status == "cancelled"
    assert cancelled_flag is True


@pytest.mark.asyncio
async def test_task_manager_api_endpoints():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-12345")
    app.include_router(tasks_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Active endpoint
        res = await client.get("/api/tasks/active")
        assert res.status_code == 200
        data = res.json()
        assert "active" in data
        assert "locks" in data
        assert "active_count" in data

        # 2. Full endpoint
        res = await client.get("/api/tasks")
        assert res.status_code == 200
        data = res.json()
        assert "active" in data
        assert "history" in data
        assert "locks" in data
