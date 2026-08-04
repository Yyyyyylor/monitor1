"""监控并发测试 — 有界并发 + 抖动不破坏结果统计（回归 P4）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.config import settings
from src.scheduler import monitor as monitor_mod


def _make_user(steam_id: str) -> SimpleNamespace:
    return SimpleNamespace(steam_id=steam_id, nickname=f"u{steam_id}")


async def test_monitor_processes_all_users(mocker) -> None:
    """全部用户成功时统计正确，且每个用户都回调 on_user_done。"""
    mocker.patch.object(settings, "fetch_concurrency", 3)
    mocker.patch.object(settings, "fetch_jitter_seconds", 0.0)
    users = [_make_user(str(i)) for i in range(6)]
    mocker.patch.object(monitor_mod, "get_active_users", mocker.AsyncMock(return_value=users))
    mocker.patch.object(monitor_mod, "_process_single_user", mocker.AsyncMock(return_value=1))

    done: list[dict] = []

    async def _on_done(result) -> None:
        done.append(result)

    stats = await monitor_mod.monitor_all_users(on_user_done=_on_done)

    assert stats["success"] == 6
    assert stats["fail"] == 0
    assert stats["total_events"] == 6
    assert len(done) == 6


async def test_concurrency_capped(mocker) -> None:
    """并发不超过 fetch_concurrency，且所有用户都被处理。"""
    mocker.patch.object(settings, "fetch_concurrency", 3)
    mocker.patch.object(settings, "fetch_jitter_seconds", 0.0)
    users = [_make_user(str(i)) for i in range(8)]
    mocker.patch.object(monitor_mod, "get_active_users", mocker.AsyncMock(return_value=users))

    active = 0
    max_active = 0

    async def fake_process(steam_id: str) -> int:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return 0

    mocker.patch.object(monitor_mod, "_process_single_user", fake_process)

    stats = await monitor_mod.monitor_all_users()

    assert max_active <= 3
    assert stats["success"] == 8
    assert stats["fail"] == 0


async def test_monitor_failures_counted(mocker) -> None:
    """单用户异常时记为失败、记录失败次数并触发管理员告警，不影响其他用户。"""
    mocker.patch.object(settings, "fetch_concurrency", 3)
    mocker.patch.object(settings, "fetch_jitter_seconds", 0.0)
    users = [_make_user(str(i)) for i in range(4)]
    mocker.patch.object(monitor_mod, "get_active_users", mocker.AsyncMock(return_value=users))
    mocker.patch.object(
        monitor_mod, "_process_single_user",
        mocker.AsyncMock(side_effect=RuntimeError("boom")),
    )
    record_failure = mocker.patch.object(
        monitor_mod, "record_failure", mocker.AsyncMock(return_value=1)
    )
    check_alert = mocker.patch.object(monitor_mod, "_check_admin_alert", mocker.AsyncMock())

    stats = await monitor_mod.monitor_all_users()

    assert stats["success"] == 0
    assert stats["fail"] == 4
    assert stats["total_events"] == 0
    assert record_failure.call_count == 4
    assert check_alert.call_count == 4
