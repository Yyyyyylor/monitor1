"""核心监控任务 — 支持分层调度（tiered scheduling）与向后兼容的统一间隔模式。

统一间隔模式（tiered_scheduling_enabled=False）：
  沿用旧版 monitor_all_users：所有用户按 fetch_interval_minutes 统一抓取。

分层调度模式（tiered_scheduling_enabled=True）：
  三级独立队列（high/medium/low），各自按配置间隔运行，互不阻塞。
  - 高频队列: tier_high_interval_minutes   (默认 5 分钟)
  - 中频队列: tier_medium_interval_minutes (默认 20 分钟)
  - 低频队列: tier_low_interval_minutes    (默认 1 小时)
  用户可随时通过 API 切换所属层级，切换在下一轮生效（平滑过渡）。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from src.config import settings
from src.crawler import fetch_inventory_paginated, parse_inventory_response
from src.db.repository import (
    get_active_users,
    get_active_users_by_frequency,
    load_current_snapshot,
    record_failure,
    reset_failure_count,
    save_snapshot_and_changes,
    save_daily_archive,
)
from src.detector import detect_changes, analyze_activity
from src.models.item import InventorySnapshot
from src.notifications.admin_notifier import notify_admin
from src.notifications.user_notifier import notify_user_change

logger = logging.getLogger(__name__)

# 全局互斥锁 — 防止任务重叠
_monitor_lock = asyncio.Lock()
# 分层调度锁（每层独立）
_tier_locks: dict[str, asyncio.Lock] = {
    "high": asyncio.Lock(),
    "medium": asyncio.Lock(),
    "low": asyncio.Lock(),
}
# 分层最近运行时间
_tier_last_run: dict[str, str] = {"high": "", "medium": "", "low": ""}
_tier_last_stats: dict[str, dict[str, Any]] = {"high": {}, "medium": {}, "low": {}}

# 供外部查询的 Callback
OnUserDone = Callable[[dict[str, Any]], Awaitable[None]] | None


async def monitor_all_users(
    on_user_done: OnUserDone = None,
) -> dict[str, int]:
    """主监控循环：处理所有活跃用户（统一间隔模式）。

    返回统计信息: {"success": int, "fail": int, "total_events": int, "elapsed_sec": float}
    """
    if _monitor_lock.locked():
        logger.warning("上一次监控任务尚未结束，跳过本次触发")
        return {"skipped": 1}

    async with _monitor_lock:
        return await _do_monitor_users(on_user_done)


async def monitor_tier(
    tier: str,
    on_user_done: OnUserDone = None,
) -> dict[str, Any]:
    """分层调度：处理指定层级的所有活跃用户。

    tier 必须是 "high" | "medium" | "low"。
    """
    lock = _tier_locks.get(tier)
    if lock is None:
        return {"skipped": 1, "error": f"unknown tier: {tier}"}

    if lock.locked():
        logger.warning("层级 %s 上一次任务尚未结束，跳过本次触发", tier)
        return {"skipped": 1, "tier": tier}

    async with lock:
        users = await get_users_for_tier(tier)
        if not users:
            return {"success": 0, "fail": 0, "total_events": 0, "tier": tier, "elapsed_sec": 0}
        return await _do_monitor_users(on_user_done, users=users)


async def get_users_for_tier(tier: str) -> list[Any]:
    """获取指定层级的活跃用户列表。"""
    grouped = await get_active_users_by_frequency()
    return grouped.get(tier, [])


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


async def _do_monitor_users(
    on_user_done: OnUserDone = None,
    users: list[Any] | None = None,
) -> dict[str, Any]:
    """内部实现：处理给定的用户列表。

    P4 优化：有界并发（asyncio.Semaphore）+ 每用户随机抖动。
      并发数受 settings.fetch_concurrency 限制（Steam 对同 IP 并发敏感，
      默认保守取 3，可结合实测调整），每用户起始加入随机延时，避免所有
      请求对齐到 Steam 限流窗口。整轮耗时从「串行求和」降为
      「ceil(用户数 / 并发) × 单用户耗时」。
    """
    start_ts = time.time()
    stats: dict[str, Any] = {"success": 0, "fail": 0, "total_events": 0}

    if users is None:
        users_record = await get_active_users()
        users = list(users_record)
    if not users:
        logger.info("没有活跃用户需要监控")
        return stats

    # 并发与抖动参数（钳制到合理区间，防止配置越界）
    concurrency = max(1, min(int(settings.fetch_concurrency), 8))
    jitter_max = max(0.0, float(settings.fetch_jitter_seconds))
    logger.info("本轮抓取: %d 个用户, 并发 %d, 抖动上限 %.1fs",
                len(users), concurrency, jitter_max)

    results: list[dict[str, Any]] = []
    user_queue: asyncio.Queue[Any | None] = asyncio.Queue()
    for user in users:
        user_queue.put_nowait(user)

    async def _run_worker() -> None:
        while True:
            user = await user_queue.get()
            if user is None:
                user_queue.task_done()
                return
            try:
        # 每用户随机抖动，打散请求起始时间，避免对齐 Steam 限流窗口
                if jitter_max > 0:
                    await asyncio.sleep(random.uniform(0, jitter_max))
                result = await _process_one_user(user)
                results.append(result)
                if on_user_done:
                    try:
                        await on_user_done(result)
                    except Exception:
                        # 回调（例如 WebSocket 推送）不应影响抓取结果，但必须可排查。
                        logger.warning("用户 %s 完成回调失败", result["steam_id"], exc_info=True)
            finally:
                user_queue.task_done()

    worker_count = min(concurrency, len(users))
    workers = [asyncio.create_task(_run_worker()) for _ in range(worker_count)]
    try:
        await user_queue.join()
        for _ in workers:
            user_queue.put_nowait(None)
        await asyncio.gather(*workers)
    finally:
        # ``Queue.join()`` does not own the worker tasks.  If the monitor is
        # cancelled while it is waiting, make sure no worker survives to use
        # the database or HTTP client after the application has closed them.
        pending_workers = [worker for worker in workers if not worker.done()]
        for worker in pending_workers:
            worker.cancel()
        if pending_workers:
            await asyncio.gather(*pending_workers, return_exceptions=True)

    for result in results:
        if result["ok"]:
            stats["success"] += 1
            stats["total_events"] += result["events"]
        else:
            stats["fail"] += 1

    elapsed = time.time() - start_ts
    stats["elapsed_sec"] = round(elapsed, 2)
    logger.info(
        "本轮监控完成: 成功 %d / 失败 %d, 事件 %d, 耗时 %.1fs",
        stats["success"], stats["fail"], stats["total_events"], elapsed,
    )

    settings.last_success_time = datetime.now(timezone.utc).isoformat()
    settings.last_fail_count = stats["fail"]

    return stats


async def _process_one_user(user: Any) -> dict[str, Any]:
    """处理单个用户，返回结果字典；内部捕获所有异常，绝不抛出。

    由 _do_monitor_users 的并发 worker 调用（每个用户一个协程）。
    """
    result = {
        "steam_id": user.steam_id,
        "nickname": user.nickname or "",
        "ok": False,
        "msg": "",
        "events": 0,
    }
    try:
        event_count = await asyncio.wait_for(
            _process_single_user(user.steam_id),
            timeout=120.0,
        )
        result["ok"] = True
        result["msg"] = "成功"
        result["events"] = event_count or 0
    except asyncio.TimeoutError:
        logger.error("用户 %s 处理超时（120s）", user.steam_id)
        fails = await record_failure(user.steam_id, "处理超时")
        result["msg"] = "超时"
        await _check_admin_alert(user.steam_id, fails)
    except Exception as exc:
        logger.exception("用户 %s 处理异常: %s", user.steam_id, exc)
        fails = await record_failure(user.steam_id, str(exc)[:500])
        result["msg"] = str(exc)[:100]
        await _check_admin_alert(user.steam_id, fails)
    return result


async def _process_single_user(steam_id: str) -> int:
    """处理单个用户的完整流程。"""
    logger.info("开始处理用户 %s", steam_id)

    # 1. 抓取库存
    raw = await fetch_inventory_paginated(steam_id)
    if raw is None:
        raise RuntimeError("无法获取库存（私密库存或 API 错误）")

    # 2. 解析
    current_items = parse_inventory_response(raw)
    if current_items is None:
        raise RuntimeError("库存解析结果为空")

    logger.info("用户 %s 库存 %d 件物品", steam_id, len(current_items))

    # 3. 加载当前基准
    previous_snapshot = await load_current_snapshot(steam_id)

    api_total = raw.get("total_inventory_count", len(current_items))

    if previous_snapshot is None:
        snapshot = InventorySnapshot(
            steam_id=steam_id,
            items=current_items,
            item_count=len(current_items),
            api_total_count=api_total,
        )
        await save_snapshot_and_changes(snapshot, [])
        logger.info("用户 %s 初始基准快照已创建 (%d 件, total=%d)", steam_id, len(current_items), api_total)
        await reset_failure_count(steam_id)
        return 0

    # 4. 差异检测
    events = detect_changes(
        steam_id=steam_id,
        previous_items=previous_snapshot.items,
        current_items=current_items,
    )

    # 5. 原子存储
    new_snapshot = InventorySnapshot(
        steam_id=steam_id,
        items=current_items,
        item_count=len(current_items),
        api_total_count=api_total,
    )
    await save_snapshot_and_changes(new_snapshot, events)

    # 6. 活动分类 + 通知
    if events:
        activity = analyze_activity(
            events=events,
            prev_total=previous_snapshot.api_total_count,
            prev_returned=previous_snapshot.item_count,
            new_total=api_total,
            new_returned=len(current_items),
        )
        logger.info("用户 %s %s", steam_id, activity.summary_line())
        await notify_user_change(steam_id, events, activity)

    await reset_failure_count(steam_id)
    logger.info("用户 %s 处理完成: %d 个变化事件", steam_id, len(events))
    return len(events)


async def _check_admin_alert(steam_id: str, fails: int) -> None:
    threshold = settings.consecutive_fail_threshold
    if threshold > 0 and fails >= threshold:
        await notify_admin(
            subject=f"用户 {steam_id} 连续失败 {fails} 次",
            body=f"连续失败次数已达阈值 {threshold}",
            extra={"steam_id": steam_id, "consecutive_fails": fails},
        )


async def compact_maintenance() -> None:
    """每日维护任务：归档 + 清理过期数据。"""
    logger.info("开始执行每日维护...")
    users = await get_active_users()
    for user in users:
        snapshot = await load_current_snapshot(user.steam_id)
        if snapshot:
            await save_daily_archive(user.steam_id, snapshot.items)

    from src.db.repository import cleanup_old_changes, cleanup_old_archives
    deleted_changes = await cleanup_old_changes()
    deleted_archives = await cleanup_old_archives()
    logger.info(
        "每日维护完成: 归档 %d 用户, 清理变化事件 %d 条, 清理归档 %d 条",
        len(users), deleted_changes, deleted_archives,
    )


# ---------------------------------------------------------------------------
# 分层状态查询（供 API 使用）
# ---------------------------------------------------------------------------

def record_tier_run(tier: str, stats: dict[str, Any]) -> str:
    """记录某层级的最近运行时间与统计，返回运行时间。

    供 Web 层调用，取代其直接改写模块级私有状态（_tier_last_run/_tier_last_stats）。
    """
    run_time = datetime.now(timezone.utc).isoformat()
    _tier_last_run[tier] = run_time
    _tier_last_stats[tier] = stats
    return run_time


def get_tier_status() -> dict[str, Any]:
    """返回各层级调度状态，供前端面板展示。"""
    return {
        "enabled": settings.tiered_scheduling_enabled,
        "intervals": {
            "high": settings.tier_high_interval_minutes,
            "medium": settings.tier_medium_interval_minutes,
            "low": settings.tier_low_interval_minutes,
        },
        "last_runs": dict(_tier_last_run),
        "last_stats": dict(_tier_last_stats),
        "spacing_seconds": settings.tier_user_spacing_seconds,
    }
