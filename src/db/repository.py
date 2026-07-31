"""数据访问层 — 封装所有数据库操作。"""

from __future__ import annotations

import asyncio
import json
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from src.config import settings
from src.db.database import async_session_factory
from src.db.models import CurrentInventoryState, InventoryChange, MonitoredUser, SnapshotArchive
from src.models.item import ChangeEvent, ChangeType, InventorySnapshot, Item
from src.utils import iso_utc as _iso_utc


# ---------------------------------------------------------------------------
# 压缩 / 解压工具
# ---------------------------------------------------------------------------

def _compress(data: Any) -> bytes:
    return zlib.compress(json.dumps(data, default=str).encode("utf-8"))


def _compress_fast(data: Any) -> bytes:
    """快速压缩（level=1），适用于变化事件的小数据。"""
    return zlib.compress(json.dumps(data, default=str).encode("utf-8"), level=1)


def _decompress(data: bytes) -> Any:
    return json.loads(zlib.decompress(data).decode("utf-8"))


# ---------------------------------------------------------------------------
# 快照读取缓存（LRU + TTL 60 秒）
# ---------------------------------------------------------------------------

from collections import OrderedDict

_snapshot_cache: OrderedDict[str, tuple[float, InventorySnapshot]] = OrderedDict()
_CACHE_TTL = 60  # 秒
_CACHE_MAX = 128  # 最大缓存数量


def _cache_get(steam_id: str) -> InventorySnapshot | None:
    """从缓存读取快照，过期返回 None。"""
    if steam_id in _snapshot_cache:
        ts, snap = _snapshot_cache[steam_id]
        if time.time() - ts < _CACHE_TTL:
            _snapshot_cache.move_to_end(steam_id)  # LRU: 访问时移到最后
            return snap
        del _snapshot_cache[steam_id]
    return None


def _cache_put(steam_id: str, snap: InventorySnapshot) -> None:
    """写入缓存，超过上限时淘汰最久未访问的条目。"""
    if steam_id in _snapshot_cache:
        _snapshot_cache.move_to_end(steam_id)
        _snapshot_cache[steam_id] = (time.time(), snap)
    else:
        if len(_snapshot_cache) >= _CACHE_MAX:
            _snapshot_cache.popitem(last=False)  # 淘汰最久未访问
        _snapshot_cache[steam_id] = (time.time(), snap)


def _cache_invalidate(steam_id: str) -> None:
    """使缓存失效。"""
    _snapshot_cache.pop(steam_id, None)


# ---------------------------------------------------------------------------
# 监控用户
# ---------------------------------------------------------------------------

async def get_active_users() -> list[MonitoredUser]:
    """获取活跃用户（排除回收站中的）。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(MonitoredUser).where(
                MonitoredUser.is_active.is_(True),
                MonitoredUser.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())


async def get_trash_users() -> list[MonitoredUser]:
    """获取回收站中的用户。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(MonitoredUser)
            .where(MonitoredUser.deleted_at.is_not(None))
            .order_by(MonitoredUser.deleted_at.desc())
        )
        return list(result.scalars().all())


async def soft_delete_user(steam_id: str) -> bool:
    """将用户移入回收站（软删除）。保留所有数据。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(MonitoredUser).where(MonitoredUser.steam_id == steam_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return False
        user.deleted_at = datetime.now(timezone.utc)
        await session.commit()
        return True


async def restore_user(steam_id: str) -> bool:
    """从回收站恢复用户。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(MonitoredUser).where(MonitoredUser.steam_id == steam_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return False
        user.deleted_at = None
        await session.commit()
        return True


async def permanent_delete_user(steam_id: str) -> bool:
    """永久删除用户及其所有数据（不可恢复）。"""
    from sqlalchemy import delete as sql_delete
    async with async_session_factory() as session:
        await session.execute(sql_delete(MonitoredUser).where(MonitoredUser.steam_id == steam_id))
        await session.execute(sql_delete(CurrentInventoryState).where(CurrentInventoryState.steam_id == steam_id))
        await session.execute(sql_delete(InventoryChange).where(InventoryChange.steam_id == steam_id))
        await session.execute(sql_delete(SnapshotArchive).where(SnapshotArchive.steam_id == steam_id))
        await session.commit()
        _cache_invalidate(steam_id)
        return True


async def upsert_user(steam_id: str, nickname: str | None = None) -> MonitoredUser:
    """添加或更新用户。如果用户在回收站中，自动恢复。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(MonitoredUser).where(MonitoredUser.steam_id == steam_id)
        )
        user = result.scalar_one_or_none()
        if user:
            if nickname:
                user.nickname = nickname
            if user.deleted_at is not None:
                user.deleted_at = None  # 从回收站恢复
        else:
            user = MonitoredUser(steam_id=steam_id, nickname=nickname)
            session.add(user)
        await session.commit()
        return user


async def record_failure(steam_id: str, error_msg: str) -> int:
    """增加失败计数并记录错误信息，返回更新后的连续失败次数。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(MonitoredUser).where(MonitoredUser.steam_id == steam_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return 0
        user.consecutive_fails = (user.consecutive_fails or 0) + 1
        user.last_error_msg = error_msg
        await session.commit()
        return user.consecutive_fails


async def reset_failure_count(steam_id: str) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(MonitoredUser).where(MonitoredUser.steam_id == steam_id)
        )
        user = result.scalar_one_or_none()
        if user:
            user.consecutive_fails = 0
            user.last_error_msg = None
            await session.commit()


# ---------------------------------------------------------------------------
# 当前库存快照
# ---------------------------------------------------------------------------

async def save_current_snapshot(snapshot: InventorySnapshot) -> None:
    """保存当前库存基准快照（全量替换），并使缓存失效。"""
    items_data = {aid: item.to_dict() for aid, item in snapshot.items.items()}
    compressed = _compress(items_data)
    async with async_session_factory() as session:
        result = await session.execute(
            select(CurrentInventoryState).where(
                CurrentInventoryState.steam_id == snapshot.steam_id
            )
        )
        record = result.scalar_one_or_none()
        if record:
            record.snapshot_data = compressed
            record.item_count = snapshot.item_count
            record.api_total_count = snapshot.api_total_count
            record.updated_at = datetime.now(timezone.utc)
        else:
            record = CurrentInventoryState(
                steam_id=snapshot.steam_id,
                snapshot_data=compressed,
                item_count=snapshot.item_count,
                api_total_count=snapshot.api_total_count,
            )
            session.add(record)
        await session.commit()
    _cache_invalidate(snapshot.steam_id)


async def load_current_snapshot(steam_id: str) -> InventorySnapshot | None:
    """加载当前库存基准快照（带缓存），不存在时返回 None。"""
    cached = _cache_get(steam_id)
    if cached is not None:
        return cached
    async with async_session_factory() as session:
        result = await session.execute(
            select(CurrentInventoryState).where(
                CurrentInventoryState.steam_id == steam_id
            )
        )
        record = result.scalar_one_or_none()
        if not record:
            return None
    items_raw = _decompress(record.snapshot_data)
    items = {aid: Item.from_dict(data) for aid, data in items_raw.items()}
    snap = InventorySnapshot(
        steam_id=steam_id,
        items=items,
        captured_at=record.updated_at,
        item_count=record.item_count,
        api_total_count=record.api_total_count,
    )
    _cache_put(steam_id, snap)
    return snap


# ---------------------------------------------------------------------------
# 变化事件
# ---------------------------------------------------------------------------

async def append_change(event: ChangeEvent) -> int:
    """保存一条变化事件，返回自增 ID。"""
    async with async_session_factory() as session:
        record = InventoryChange(
            steam_id=event.steam_id,
            change_type=event.change_type,
            asset_id=event.asset_id,
            old_asset_id=event.old_asset_id,
            detail=_compress(event.detail) if event.detail else None,
            snapshot_before=_compress(event.snapshot_before) if event.snapshot_before else None,
        )
        session.add(record)
        await session.commit()
        return record.id


async def append_changes(events: list[ChangeEvent]) -> int:
    """批量保存变化事件（单次事务），返回写入数量。"""
    if not events:
        return 0
    async with async_session_factory() as session:
        for event in events:
            record = InventoryChange(
                steam_id=event.steam_id,
                change_type=event.change_type,
                asset_id=event.asset_id,
                old_asset_id=event.old_asset_id,
                detail=_compress_fast(event.detail) if event.detail else None,
                snapshot_before=_compress_fast(event.snapshot_before) if event.snapshot_before else None,
            )
            session.add(record)
        await session.commit()
        return len(events)


async def save_snapshot_and_changes(
    snapshot: InventorySnapshot, events: list[ChangeEvent]
) -> None:
    """原子事务：保存快照 + 批量写入变化事件。

    保证两者要么同时成功，要么同时回滚，不会出现快照已更新但事件丢失。
    """
    items_data = {aid: item.to_dict() for aid, item in snapshot.items.items()}
    compressed = _compress(items_data)

    async with async_session_factory() as session:
        # --- 快照 ---
        result = await session.execute(
            select(CurrentInventoryState).where(
                CurrentInventoryState.steam_id == snapshot.steam_id
            )
        )
        record = result.scalar_one_or_none()
        if record:
            record.snapshot_data = compressed
            record.item_count = snapshot.item_count
            record.api_total_count = snapshot.api_total_count
            record.updated_at = datetime.now(timezone.utc)
        else:
            record = CurrentInventoryState(
                steam_id=snapshot.steam_id,
                snapshot_data=compressed,
                item_count=snapshot.item_count,
                api_total_count=snapshot.api_total_count,
            )
            session.add(record)

        # --- 变化事件 ---
        for event in events:
            ch = InventoryChange(
                steam_id=event.steam_id,
                change_type=event.change_type,
                asset_id=event.asset_id,
                old_asset_id=event.old_asset_id,
                detail=_compress_fast(event.detail) if event.detail else None,
                snapshot_before=_compress_fast(event.snapshot_before) if event.snapshot_before else None,
            )
            session.add(ch)

        await session.commit()

    _cache_invalidate(snapshot.steam_id)


async def get_recent_changes(
    steam_id: str, limit: int = 500
) -> list[dict[str, Any]]:
    """获取某用户最近的变化事件。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(InventoryChange)
            .where(InventoryChange.steam_id == steam_id)
            .order_by(InventoryChange.change_time.desc())
            .limit(limit)
        )
        records = result.scalars().all()
    events = []
    for r in records:
        events.append({
            "id": r.id,
            "change_type": r.change_type.value,
            "asset_id": r.asset_id,
            "old_asset_id": r.old_asset_id,
            "change_time": _iso_utc(r.change_time),
            "detail": _decompress(r.detail) if r.detail else None,
        })
    return events


async def cleanup_old_changes() -> int:
    """清理超过保留天数的变化事件，返回删除行数。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.change_retention_days)
    async with async_session_factory() as session:
        result = await session.execute(
            delete(InventoryChange).where(InventoryChange.change_time < cutoff)
        )
        await session.commit()
        return result.rowcount


# ---------------------------------------------------------------------------
# 归档快照
# ---------------------------------------------------------------------------

async def save_daily_archive(steam_id: str, items: dict[str, Item], force: bool = False) -> None:
    """保存库存快照归档。

    Args:
        force: True 时允许同一天创建多条（手动快照），False 时每天只存一条（自动归档）。
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    items_data = {aid: item.to_dict() for aid, item in items.items()}
    async with async_session_factory() as session:
        if not force:
            result = await session.execute(
                select(SnapshotArchive).where(
                    SnapshotArchive.steam_id == steam_id,
                    SnapshotArchive.captured_at >= today_start,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                return  # 当天已归档，不再重复
        archive = SnapshotArchive(
            steam_id=steam_id,
            captured_at=now,
            snapshot_data=_compress(items_data),
        )
        session.add(archive)
        await session.commit()


async def cleanup_old_archives() -> int:
    """清理超过保留天数的归档快照。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.archive_retention_days)
    async with async_session_factory() as session:
        result = await session.execute(
            delete(SnapshotArchive).where(SnapshotArchive.captured_at < cutoff)
        )
        await session.commit()
        return result.rowcount


# ---------------------------------------------------------------------------
# 初始化默认用户
# ---------------------------------------------------------------------------

async def init_default_users() -> None:
    """从配置创建默认监控用户。已在回收站中的用户不会被恢复。"""
    from sqlalchemy import select as sql_select
    for sid in settings.steam_id_list:
        async with async_session_factory() as session:
            result = await session.execute(
                sql_select(MonitoredUser).where(MonitoredUser.steam_id == sid)
            )
            existing = result.scalar_one_or_none()
        if existing is None:
            # 完全不存在才创建
            await upsert_user(sid)
        # 已存在（包括在回收站中的）不操作，由用户通过 Web 界面手动恢复


# ---------------------------------------------------------------------------
# 分层调度：按频率获取用户 + 设置频率
# ---------------------------------------------------------------------------

async def get_active_users_by_frequency() -> dict[str, list[MonitoredUser]]:
    """返回按监控频率分组的活跃用户：{frequency: [users]}。

    仅在 tiered_scheduling_enabled=True 时被调度器使用。
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(MonitoredUser).where(
                MonitoredUser.is_active.is_(True),
                MonitoredUser.deleted_at.is_(None),
            )
        )
        users = result.scalars().all()

    grouped: dict[str, list[MonitoredUser]] = {"high": [], "medium": [], "low": []}
    for u in users:
        freq = getattr(u, "monitor_frequency", "medium") or "medium"
        if freq not in grouped:
            freq = "medium"
        grouped[freq].append(u)
    return grouped


async def set_user_frequency(steam_id: str, frequency: str) -> bool:
    """设置用户的监控频率层级。frequency 必须是 high/medium/low 之一。"""
    if frequency not in ("high", "medium", "low"):
        return False
    async with async_session_factory() as session:
        result = await session.execute(
            select(MonitoredUser).where(MonitoredUser.steam_id == steam_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return False
        user.monitor_frequency = frequency
        await session.commit()
        return True


# ---------------------------------------------------------------------------
# 数据导出 / 导入
# ---------------------------------------------------------------------------

EXPORT_VERSION = "1.0"


async def export_all_data() -> dict[str, Any]:
    """导出全部监控数据为字典（供 API 序列化为 JSON 文件）。

    使用分批查询避免一次性加载全部数据到内存。
    """
    async with async_session_factory() as session:
        # 所有用户（含回收站）
        user_result = await session.execute(select(MonitoredUser))
        all_users = user_result.scalars().all()

        # 所有当前库存
        inv_result = await session.execute(select(CurrentInventoryState))
        inventories: dict[str, CurrentInventoryState] = {i.steam_id: i for i in inv_result.scalars().all()}

        # 所有历史快照
        arch_result = await session.execute(
            select(SnapshotArchive).order_by(SnapshotArchive.captured_at.desc())
        )
        archives: dict[str, list[SnapshotArchive]] = {}
        for a in arch_result.scalars().all():
            archives.setdefault(a.steam_id, []).append(a)

        # 最近变化事件（每用户最多 200 条，分批构建）
        ch_result = await session.execute(
            select(InventoryChange).order_by(InventoryChange.change_time.desc())
        )
        changes: dict[str, list[InventoryChange]] = {}
        for c in ch_result.scalars().all():
            lst = changes.setdefault(c.steam_id, [])
            if len(lst) < 200:
                lst.append(c)

    # 分批处理每个用户的数据，避免同时解压所有快照
    users_data: list[dict[str, Any]] = []
    for u in all_users:
        entry: dict[str, Any] = {
            "steam_id": u.steam_id,
            "nickname": u.nickname,
            "is_active": u.is_active,
            "monitor_frequency": getattr(u, "monitor_frequency", "medium") or "medium",
            "created_at": _iso_utc(u.created_at) if u.created_at else None,
            "deleted_at": _iso_utc(u.deleted_at) if u.deleted_at else None,
        }
        # 当前库存（逐用户解压，处理完即可释放）
        inv = inventories.get(u.steam_id)
        if inv:
            entry["current_inventory"] = {
                "item_count": inv.item_count,
                "api_total_count": inv.api_total_count,
                "updated_at": _iso_utc(inv.updated_at) if inv.updated_at else None,
                "items": _decompress(inv.snapshot_data) if inv.snapshot_data else [],
            }
        # 变化事件
        ch_list = changes.get(u.steam_id, [])
        entry["recent_changes"] = [
            {
                "change_type": c.change_type.value if hasattr(c.change_type, 'value') else str(c.change_type),
                "asset_id": c.asset_id,
                "old_asset_id": c.old_asset_id,
                "detail": _decompress(c.detail) if c.detail else None,
                "snapshot_before": _decompress(c.snapshot_before) if c.snapshot_before else None,
                "change_time": _iso_utc(c.change_time) if c.change_time else None,
            }
            for c in ch_list
        ]
        # 历史快照
        arch_list = archives.get(u.steam_id, [])
        entry["archives"] = [
            {
                "captured_at": _iso_utc(a.captured_at) if a.captured_at else None,
                "items": _decompress(a.snapshot_data) if a.snapshot_data else [],
            }
            for a in arch_list
        ]
        users_data.append(entry)
        # 每处理 10 个用户让出一次控制权，避免阻塞事件循环
        if len(users_data) % 10 == 0:
            await asyncio.sleep(0)

    return {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "app": "steam-cs2-inventory-monitor",
        "user_count": len(users_data),
        "users": users_data,
    }


async def import_all_data(data: dict[str, Any]) -> dict[str, int]:
    """从导出字典导入数据。返回 {"created": int, "updated": int, "skipped": int}。"""
    version = data.get("version", "")
    if version != EXPORT_VERSION:
        raise ValueError(f"Unsupported export version: {version!r}")

    users_list: list[dict[str, Any]] = data.get("users", [])
    stats = {"created": 0, "updated": 0, "skipped": 0}

    for entry in users_list:
        sid = entry.get("steam_id", "")
        if not sid:
            stats["skipped"] += 1
            continue

        async with async_session_factory() as session:
            result = await session.execute(
                select(MonitoredUser).where(MonitoredUser.steam_id == sid)
            )
            user = result.scalar_one_or_none()
            if user is None:
                user = MonitoredUser(steam_id=sid)
                session.add(user)
                stats["created"] += 1
            else:
                stats["updated"] += 1

            user.nickname = entry.get("nickname") or user.nickname
            user.is_active = entry.get("is_active", True)
            freq = entry.get("monitor_frequency", "medium")
            if freq in ("high", "medium", "low"):
                user.monitor_frequency = freq
            # 删除状态：如果源数据标记了 delete_at 且本地没有，保留本地的
            if entry.get("deleted_at") and user.deleted_at is None:
                pass  # 不覆盖本地状态
            await session.commit()

            # 当前库存
            inv_data = entry.get("current_inventory")
            if inv_data and inv_data.get("items"):
                items_json = _compress(inv_data["items"])
                existing_inv = await session.execute(
                    select(CurrentInventoryState).where(CurrentInventoryState.steam_id == sid)
                )
                old_inv = existing_inv.scalar_one_or_none()
                if old_inv:
                    old_inv.snapshot_data = items_json
                    old_inv.item_count = inv_data.get("item_count", len(inv_data["items"]))
                    old_inv.api_total_count = inv_data.get("api_total_count", 0)
                    old_inv.updated_at = datetime.now(timezone.utc)
                else:
                    session.add(CurrentInventoryState(
                        steam_id=sid,
                        snapshot_data=items_json,
                        item_count=inv_data.get("item_count", len(inv_data["items"])),
                        api_total_count=inv_data.get("api_total_count", 0),
                    ))
                await session.commit()

            # 变化事件
            for ch in entry.get("recent_changes", []):
                detail = _compress(ch["detail"]) if ch.get("detail") else None
                snap = _compress(ch["snapshot_before"]) if ch.get("snapshot_before") else None
                ct = ch.get("change_type", "added")
                if isinstance(ct, str):
                    try:
                        ct = ChangeType(ct)
                    except ValueError:
                        ct = ChangeType.MODIFIED  # fallback for unknown values
                session.add(InventoryChange(
                    steam_id=sid,
                    change_type=ct,
                    asset_id=ch.get("asset_id", ""),
                    old_asset_id=ch.get("old_asset_id"),
                    detail=detail,
                    snapshot_before=snap,
                ))

            # 历史快照
            for arch in entry.get("archives", []):
                captured_str = arch.get("captured_at", "")
                if captured_str:
                    try:
                        captured_dt = datetime.fromisoformat(captured_str)
                    except ValueError:
                        captured_dt = datetime.now(timezone.utc)
                else:
                    captured_dt = datetime.now(timezone.utc)
                items_compressed = _compress(arch.get("items", []))
                session.add(SnapshotArchive(
                    steam_id=sid,
                    captured_at=captured_dt,
                    snapshot_data=items_compressed,
                ))

            await session.commit()

    return stats


# ---------------------------------------------------------------------------
# 登录限速（持久化到数据库）
# ---------------------------------------------------------------------------

async def get_login_rate_limit(client_ip: str) -> tuple[int, float]:
    """获取 IP 的登录失败计数和锁定截止时间戳。

    Returns:
        (attempts, lock_until_timestamp)
    """
    from src.db.models import LoginRateLimit
    async with async_session_factory() as session:
        result = await session.execute(
            select(LoginRateLimit).where(LoginRateLimit.client_ip == client_ip)
        )
        record = result.scalar_one_or_none()
        if not record:
            return (0, 0.0)
        lock_ts = record.lock_until.timestamp() if record.lock_until else 0.0
        return (record.attempts, lock_ts)


async def update_login_rate_limit(
    client_ip: str, attempts: int, lock_until: datetime | None
) -> None:
    """更新 IP 的登录失败计数和锁定截止时间。"""
    from src.db.models import LoginRateLimit
    async with async_session_factory() as session:
        result = await session.execute(
            select(LoginRateLimit).where(LoginRateLimit.client_ip == client_ip)
        )
        record = result.scalar_one_or_none()
        if record:
            record.attempts = attempts
            record.lock_until = lock_until
        else:
            record = LoginRateLimit(
                client_ip=client_ip,
                attempts=attempts,
                lock_until=lock_until,
            )
            session.add(record)
        await session.commit()


async def clear_login_rate_limit(client_ip: str) -> None:
    """登录成功后清除 IP 的失败计数。"""
    from src.db.models import LoginRateLimit
    async with async_session_factory() as session:
        await session.execute(
            delete(LoginRateLimit).where(LoginRateLimit.client_ip == client_ip)
        )
        await session.commit()
