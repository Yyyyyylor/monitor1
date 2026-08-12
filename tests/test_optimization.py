"""Regression tests for backend-only, behavior-preserving optimizations."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import settings
from src.crawler import fetcher
from src.db import repository
from src.db.database import Base
from src.db.models import CurrentInventoryState, InventoryChange, MonitoredUser, SnapshotArchive
from src.scheduler import monitor as monitor_mod
from src.web import app as web_app


def _user(steam_id: str) -> SimpleNamespace:
    return SimpleNamespace(steam_id=steam_id, nickname=steam_id)


def test_retry_after_is_clamped_and_preserves_normal_values(mocker) -> None:
    mocker.patch.object(settings, "steam_retry_after_max_seconds", 300)
    assert fetcher._retry_after_seconds("45") == 45
    assert fetcher._retry_after_seconds("999999") == 300
    assert fetcher._retry_after_seconds("invalid") == 60
    mocker.patch.object(settings, "steam_retry_after_max_seconds", 0)
    assert fetcher._retry_after_seconds("999999") == 1


async def test_start_web_server_returns_runner_for_aiohttp_cleanup(mocker) -> None:
    runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock())
    site = SimpleNamespace(start=AsyncMock())
    scheduler = SimpleNamespace(start=mocker.Mock())
    mocker.patch.object(web_app, "create_web_app", return_value=object())
    mocker.patch.object(web_app.web, "AppRunner", return_value=runner)
    mocker.patch.object(web_app.web, "TCPSite", return_value=site)
    mocker.patch("apscheduler.schedulers.asyncio.AsyncIOScheduler", return_value=scheduler)
    mocker.patch.object(web_app, "_update_scheduler_jobs_internal")

    returned = await web_app.start_web_server()

    assert returned is runner
    runner.setup.assert_awaited_once()
    site.start.assert_awaited_once()


async def test_worker_queue_never_creates_more_workers_than_concurrency(mocker) -> None:
    mocker.patch.object(settings, "fetch_concurrency", 2)
    mocker.patch.object(settings, "fetch_jitter_seconds", 0.0)
    users = [_user(str(index)) for index in range(7)]
    mocker.patch.object(monitor_mod, "get_active_users", AsyncMock(return_value=users))
    mocker.patch.object(monitor_mod, "_process_single_user", AsyncMock(return_value=0))

    original_create_task = asyncio.create_task
    created = []

    def track_task(coro):
        task = original_create_task(coro)
        created.append(task)
        return task

    mocker.patch.object(monitor_mod.asyncio, "create_task", side_effect=track_task)
    stats = await monitor_mod.monitor_all_users()

    assert stats["success"] == len(users)
    assert len(created) == 2


async def test_cancelling_monitor_waits_for_all_worker_tasks(mocker) -> None:
    mocker.patch.object(settings, "fetch_concurrency", 2)
    mocker.patch.object(settings, "fetch_jitter_seconds", 0.0)
    started = asyncio.Event()
    never_finish = asyncio.Event()

    async def block_worker(_user) -> dict:
        started.set()
        await never_finish.wait()
        raise AssertionError("worker should have been cancelled")

    created: list[asyncio.Task] = []
    original_create_task = asyncio.create_task

    def track_task(coro):
        task = original_create_task(coro)
        created.append(task)
        return task

    mocker.patch.object(monitor_mod, "_process_one_user", side_effect=block_worker)
    mocker.patch.object(monitor_mod.asyncio, "create_task", side_effect=track_task)
    monitor_task = asyncio.create_task(
        monitor_mod._do_monitor_users(users=[_user("1"), _user("2"), _user("3")])
    )
    await started.wait()
    monitor_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await monitor_task

    assert created
    assert all(task.done() and task.cancelled() for task in created)


async def test_callback_exception_is_logged_and_does_not_change_stats(mocker, caplog) -> None:
    mocker.patch.object(settings, "fetch_concurrency", 1)
    mocker.patch.object(settings, "fetch_jitter_seconds", 0.0)
    mocker.patch.object(monitor_mod, "get_active_users", AsyncMock(return_value=[_user("1")]))
    mocker.patch.object(monitor_mod, "_process_single_user", AsyncMock(return_value=2))

    async def broken_callback(_result) -> None:
        raise RuntimeError("websocket unavailable")

    stats = await monitor_mod.monitor_all_users(on_user_done=broken_callback)

    assert stats["success"] == 1
    assert stats["total_events"] == 2
    assert "完成回调失败" in caplog.text


async def test_reset_failure_count_only_commits_when_a_row_changes(mocker) -> None:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    session.commit = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    mocker.patch.object(repository, "async_session_factory", return_value=_SessionContext())
    await repository.reset_failure_count("76561190000000000")

    session.commit.assert_not_awaited()


async def test_reset_failure_count_commits_a_real_update(mocker) -> None:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    session.commit = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    mocker.patch.object(repository, "async_session_factory", return_value=_SessionContext())
    await repository.reset_failure_count("76561190000000000")

    session.commit.assert_awaited_once()


async def test_batch_import_preserves_deduped_export_output(mocker, tmp_path) -> None:
    """The bulk preload path must retain the import format's previous deduplication rules."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'import.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    mocker.patch.object(repository, "async_session_factory", session_factory)
    payload = {
        "app": "steam-cs2-inventory-monitor",
        "version": repository.EXPORT_VERSION,
        "users": [
            {
                "steam_id": "76561190000000001",
                "nickname": "first",
                "is_active": True,
                "monitor_frequency": "high",
                "current_inventory": {
                    "item_count": 1,
                    "api_total_count": 1,
                    "items": {"asset-1": {"name": "A"}},
                },
                "recent_changes": [{
                    "change_type": "added",
                    "asset_id": "asset-1",
                    "old_asset_id": None,
                    "detail": {"name": "A"},
                    "snapshot_before": None,
                    "change_time": "2026-08-12T00:00:00+00:00",
                }],
                "archives": [{
                    "captured_at": "2026-08-12T00:00:00+00:00",
                    "items": {"asset-1": {"name": "A"}},
                }],
            },
            {
                "steam_id": "76561190000000002",
                "nickname": "second",
                "is_active": False,
                "monitor_frequency": "low",
                "recent_changes": [],
                "archives": [],
            },
        ],
    }
    try:
        assert await repository.import_all_data(payload) == {"created": 2, "updated": 0, "skipped": 0}
        assert await repository.import_all_data(payload) == {"created": 0, "updated": 2, "skipped": 0}
        exported = await repository.export_all_data()
        assert exported["user_count"] == 2
        first = next(item for item in exported["users"] if item["steam_id"].endswith("01"))
        assert first["current_inventory"]["items"] == {"asset-1": {"name": "A"}}
        assert len(first["recent_changes"]) == len(first["archives"]) == 1
        async with session_factory() as session:
            assert await session.scalar(repository.select(repository.func.count()).select_from(MonitoredUser)) == 2
            assert await session.scalar(repository.select(repository.func.count()).select_from(CurrentInventoryState)) == 1
            assert await session.scalar(repository.select(repository.func.count()).select_from(InventoryChange)) == 1
            assert await session.scalar(repository.select(repository.func.count()).select_from(SnapshotArchive)) == 1
    finally:
        await engine.dispose()


async def test_ws_broadcast_sends_concurrently_and_removes_dead_clients() -> None:
    class FakeWebSocket:
        def __init__(self, *, fail: bool = False) -> None:
            self.closed = False
            self.fail = fail
            self.messages: list[str] = []

        async def send_str(self, message: str) -> None:
            await asyncio.sleep(0.01)
            if self.fail:
                raise RuntimeError("closed peer")
            self.messages.append(message)

    first, dead, second = FakeWebSocket(), FakeWebSocket(fail=True), FakeWebSocket()
    previous_clients = web_app._state.ws_clients
    previous_lock = web_app._state.ws_lock
    web_app._state.ws_clients = {first, dead, second}
    web_app._state.ws_lock = asyncio.Lock()
    try:
        start = asyncio.get_running_loop().time()
        await web_app.ws_broadcast("update", {"ok": True})
        elapsed = asyncio.get_running_loop().time() - start
        # Three 10ms sends complete together; a serial implementation needs about 30ms.
        assert elapsed < 0.04
        assert len(first.messages) == len(second.messages) == 1
        assert dead not in web_app._state.ws_clients
    finally:
        web_app._state.ws_clients = previous_clients
        web_app._state.ws_lock = previous_lock


async def test_app_cleanup_releases_scheduler_clients_and_database(mocker) -> None:
    app = web_app.create_web_app()
    monitor_task = asyncio.create_task(asyncio.sleep(60))
    tier_task = asyncio.create_task(asyncio.sleep(60))
    scheduler = SimpleNamespace(shutdown=mocker.Mock())
    previous = (
        web_app._state.monitor_task,
        web_app._state.tier_tasks,
        web_app._state.scheduler_ref,
    )
    web_app._state.monitor_task = monitor_task
    web_app._state.tier_tasks = {"high": tier_task, "medium": None, "low": None}
    web_app._state.scheduler_ref = scheduler
    close_client = mocker.patch("src.crawler.fetcher.close_client", AsyncMock())
    close_db = mocker.patch.object(web_app, "close_db", AsyncMock())
    try:
        app.freeze()
        await app.on_cleanup.send(app)
        assert monitor_task.cancelled()
        assert tier_task.cancelled()
        scheduler.shutdown.assert_called_once_with(wait=False)
        close_client.assert_awaited_once()
        close_db.assert_awaited_once()
    finally:
        web_app._state.monitor_task, web_app._state.tier_tasks, web_app._state.scheduler_ref = previous


async def test_app_cleanup_cancels_running_maintenance_before_closing_db(mocker) -> None:
    app = web_app.create_web_app()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def block_maintenance() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    previous = (
        web_app._state.scheduler_ref,
        web_app._state.maintenance_task,
        web_app._state.maintenance_enabled,
    )
    scheduler = SimpleNamespace(shutdown=mocker.Mock())
    web_app._state.scheduler_ref = scheduler
    web_app._state.maintenance_enabled = True
    mocker.patch("src.scheduler.monitor.compact_maintenance", side_effect=block_maintenance)
    close_client = mocker.patch("src.crawler.fetcher.close_client", AsyncMock())

    async def assert_maintenance_stopped() -> None:
        assert stopped.is_set()

    close_db = mocker.patch.object(web_app, "close_db", side_effect=assert_maintenance_stopped)
    maintenance_task = asyncio.create_task(web_app._run_maintenance())
    await started.wait()
    try:
        app.freeze()
        await app.on_cleanup.send(app)
        assert maintenance_task.cancelled()
        scheduler.shutdown.assert_called_once_with(wait=False)
        close_client.assert_awaited_once()
        close_db.assert_awaited_once()
        await app.on_cleanup.send(app)
        close_db.assert_awaited_once()
    finally:
        web_app._state.scheduler_ref, web_app._state.maintenance_task, web_app._state.maintenance_enabled = previous


async def test_start_web_server_cleans_runner_after_all_ports_fail(mocker) -> None:
    runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock())
    site = SimpleNamespace(start=AsyncMock(side_effect=OSError("address already in use")))
    mocker.patch.object(web_app, "create_web_app", return_value=object())
    mocker.patch.object(web_app.web, "AppRunner", return_value=runner)
    mocker.patch.object(web_app.web, "TCPSite", return_value=site)

    with pytest.raises(SystemExit):
        await web_app.start_web_server()

    assert site.start.await_count == 5
    runner.cleanup.assert_awaited_once()


async def test_start_web_server_cleans_runner_after_start_exception(mocker) -> None:
    runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock())
    site = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("bind failed")))
    mocker.patch.object(web_app, "create_web_app", return_value=object())
    mocker.patch.object(web_app.web, "AppRunner", return_value=runner)
    mocker.patch.object(web_app.web, "TCPSite", return_value=site)

    with pytest.raises(RuntimeError, match="bind failed"):
        await web_app.start_web_server()

    runner.cleanup.assert_awaited_once()
