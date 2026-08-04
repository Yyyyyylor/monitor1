"""通知 HTTP 发送工具 — 带指数退避重试（回归 P10）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def post_json_with_retry(
    client: Any,
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float = 10,
    retries: int = 2,
    label: str = "通知",
) -> None:
    """POST JSON，失败时指数退避重试；重试耗尽仅记录日志，不抛异常。"""
    for attempt in range(retries + 1):
        try:
            await client.post(url, json=payload, timeout=timeout)
            return
        except Exception as exc:
            if attempt < retries:
                backoff = 1.5 ** attempt
                logger.warning(
                    "%s 发送失败 (attempt %d/%d): %s，%.1fs 后重试",
                    label, attempt + 1, retries + 1, exc, backoff,
                )
                await asyncio.sleep(backoff)
            else:
                logger.warning("%s 发送失败（重试耗尽）: %s", label, exc)
