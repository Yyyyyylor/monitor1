"""管理员通知服务 — 连续失败、任务异常等告警。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config import settings
from src.notifications._http import post_json_with_retry

logger = logging.getLogger(__name__)


async def _send_telegram(text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    from src.crawler.fetcher import get_client
    client = await get_client()
    await post_json_with_retry(
        client, url,
        {"chat_id": settings.telegram_chat_id, "text": f"⚠️ [管理员]\n{text}", "parse_mode": "HTML"},
        label="管理员 Telegram",
    )


async def _send_webhook(text: str) -> None:
    if not settings.admin_webhook_url:
        return
    from src.crawler.fetcher import get_client
    client = await get_client()
    if settings.admin_webhook_type == "dingtalk":
        payload = {"msgtype": "text", "text": {"content": f"⚠️ [管理员]\n{text}"}}
    else:
        payload = {"text": text}
    await post_json_with_retry(
        client, settings.admin_webhook_url, payload, label="管理员 Webhook",
    )


async def notify_admin(
    subject: str,
    body: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """发送管理员告警。"""
    if not settings.admin_notify_enabled:
        return

    message = f"<b>{subject}</b>\n{body}"
    if extra:
        for k, v in extra.items():
            message += f"\n{k}: {v}"

    tasks = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        tasks.append(_send_telegram(message))
    if settings.admin_webhook_url:
        tasks.append(_send_webhook(message))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    logger.warning("管理员告警已发送: %s", subject)
