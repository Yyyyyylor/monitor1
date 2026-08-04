"""用户通知服务 — 推送库存变化给最终用户。"""

from __future__ import annotations

import asyncio
import html as _html
import logging
from typing import TYPE_CHECKING, Any

from src.config import settings
from src.models.item import ChangeEvent, ChangeType
from src.notifications._http import post_json_with_retry

# 延迟导入避免循环
if TYPE_CHECKING:
    from src.detector.diff import InventoryActivity

logger = logging.getLogger(__name__)


def _extract_item_name(event: ChangeEvent) -> str:
    """从变化事件详情中提取物品名称（适配不同事件类型的嵌套结构）。"""
    # SWAPPED: market_hash_name 在顶层
    if "market_hash_name" in event.detail:
        return _html.escape(event.detail["market_hash_name"])
    # MODIFIED: market_hash_name 在 current_state 里
    if "current_state" in event.detail:
        return _html.escape(event.detail["current_state"].get("market_hash_name", "Unknown Item"))
    # ADDED / REMOVED: market_hash_name 在 item 里
    if "item" in event.detail:
        return _html.escape(event.detail["item"].get("market_hash_name", "Unknown Item"))
    return "Unknown Item"


def _format_change_message(event: ChangeEvent) -> str:
    """根据变化类型格式化通知消息。"""
    name = _extract_item_name(event)
    if event.change_type == ChangeType.ADDED:
        item = event.detail.get("item", {})
        wear = item.get("attributes", {}).get("paint_wear")
        wear_str = f" (磨损 {wear:.4f})" if wear is not None else ""
        return f"✅ 新增: {name}{wear_str}"
    elif event.change_type == ChangeType.REMOVED:
        return f"❌ 移除: {name}"
    elif event.change_type == ChangeType.MODIFIED:
        changes = event.detail.get("changes", {})
        parts = [f"{k}: {v[0]} → {v[1]}" for k, v in changes.items()]
        return f"📝 修改: {name}\n  " + "\n  ".join(parts)
    elif event.change_type == ChangeType.SWAPPED:
        attr_diffs = event.detail.get("attribute_diffs", {})
        parts = [f"{k}: {v[0]} → {v[1]}" for k, v in attr_diffs.items()]
        base = f"🔄 交换: {name}\n  旧 asset_id: {event.old_asset_id}\n  新 asset_id: {event.asset_id}"
        if parts:
            base += "\n  " + "\n  ".join(parts)
        return base
    return f"❓ 未知变化: {name} ({event.change_type.value})"


async def _send_telegram(msg: str) -> None:
    """通过 Telegram Bot API 发送消息（带重试）。"""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    from src.crawler.fetcher import get_client
    client = await get_client()
    await post_json_with_retry(
        client, url,
        {"chat_id": settings.telegram_chat_id, "text": msg, "parse_mode": "HTML"},
        label="Telegram",
    )


async def _send_dingtalk(text: str) -> None:
    """通过钉钉 Webhook 发送消息（带重试）。"""
    if not settings.dingtalk_webhook_url:
        return
    from src.crawler.fetcher import get_client
    client = await get_client()
    await post_json_with_retry(
        client, settings.dingtalk_webhook_url,
        {"msgtype": "text", "text": {"content": text}},
        label="钉钉",
    )


async def _send_serverchan(text: str) -> None:
    """通过 Server酱 发送消息（带重试）。"""
    if not settings.serverchan_key:
        return
    url = f"https://sctapi.ftqq.com/{settings.serverchan_key}.send"
    from src.crawler.fetcher import get_client
    client = await get_client()
    await post_json_with_retry(
        client, url,
        {"title": "CS2 库存变化", "content": text},
        label="Server酱",
    )


async def notify_user_change(
    steam_id: str,
    events: list[ChangeEvent],
    activity: InventoryActivity | None = None,
) -> None:
    """推送一组库存变化事件给用户。"""
    if not settings.user_notify_enabled or not events:
        return

    lines = [f"🎮 Steam {steam_id} 库存变化:"]

    # 活动分类（基于 delta 分析）
    if activity and activity.category != "unchanged":
        lines.append("")
        lines.append(f"━━━ {activity.summary_line()} ━━━")

    # 详细变化
    lines.append("")
    for ev in events:
        lines.append(_format_change_message(ev))

    message = "\n".join(lines)

    # 并发发送所有启用的渠道
    tasks = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        tasks.append(_send_telegram(message))
    if settings.dingtalk_webhook_url:
        tasks.append(_send_dingtalk(message))
    if settings.serverchan_key:
        tasks.append(_send_serverchan(message))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("用户通知已发送 (%d 事件)", len(events))
