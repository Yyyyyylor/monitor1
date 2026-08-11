"""通知 HTTP 发送工具 — 带指数退避重试（回归 P10）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

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
            response = await client.post(url, json=payload, timeout=timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if 400 <= status < 500 and status != 429:
                logger.warning("%s was rejected by the remote endpoint (HTTP %d)", label, status)
                return
            failure: Exception = exc
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            failure = exc
        except Exception as exc:
            failure = exc

        if attempt < retries:
            backoff = 1.5 ** attempt
            logger.warning(
                "%s delivery failed (attempt %d/%d): %s; retrying in %.1fs",
                label, attempt + 1, retries + 1, failure, backoff,
            )
            await asyncio.sleep(backoff)
        else:
            logger.warning("%s delivery failed after retries: %s", label, failure)
