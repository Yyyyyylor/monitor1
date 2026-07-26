"""分页爬取引擎 — 从 Steam 社区 API 抓取 CS2 库存。"""

from __future__ import annotations

import asyncio
import logging
import warnings
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 共享 HTTP 客户端（连接池复用，避免每次请求都重新建连）
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def _build_client_kwargs() -> dict[str, Any]:
    proxy_cfg = _get_proxy_config()
    proxy_url = proxy_cfg.get("proxy")
    need_verify = proxy_cfg.get("verify", True)
    kwargs: dict[str, Any] = {"trust_env": False}
    if proxy_url:
        kwargs["proxy"] = proxy_url
    if not need_verify:
        kwargs["verify"] = False
        warnings.filterwarnings("ignore", message=".*verify.*", category=UserWarning)
    return kwargs


async def get_client() -> httpx.AsyncClient:
    """获取共享的 httpx 客户端（单例，带连接池）。"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(**_build_client_kwargs())
    return _client


async def close_client() -> None:
    """关闭共享客户端（程序退出时调用）。"""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


def _get_proxy_config() -> dict[str, Any]:
    """获取代理配置（延迟加载，避免模块导入时就检测）。"""
    from src.crawler.proxy import resolve_proxy_config
    return resolve_proxy_config(
        proxy_mode=settings.steam_proxy_mode,
        proxy_url=settings.steam_proxy_url,
        hosts_override=settings.steam_hosts_override,
    )


def _build_headers(proxy_cfg: dict[str, Any]) -> dict[str, str]:
    """构建请求头，包含 Steam API 所需的浏览器特征头。"""
    headers = {
        "User-Agent": settings.steam_user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://steamcommunity.com/",
        # 以下三个是 Steam API 反爬的关键头 — 缺失会直接 429
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    # steamLoginSecure cookie（用户可在 .env 中配置，解决部分私密库存需要登录的问题）
    if settings.steam_cookie:
        headers["Cookie"] = settings.steam_cookie.strip()
    headers.update(proxy_cfg.get("headers_extra", {}))
    return headers


def _build_url(steam_id: str, proxy_cfg: dict[str, Any], start_assetid: str | None = None) -> str:
    url = settings.steam_inventory_url.format(steam_id=steam_id)
    hosts = proxy_cfg.get("hosts_override")
    if hosts:
        url = url.replace(
            "https://steamcommunity.com",
            f"https://{hosts}",
        )
    params = f"?l=english&count={settings.page_size}"
    if start_assetid:
        params += f"&start_assetid={start_assetid}"
    return url + params


async def _fetch_page(
    client: httpx.AsyncClient,
    steam_id: str,
    proxy_cfg: dict[str, Any],
    start_assetid: str | None = None,
) -> dict[str, Any] | None:
    """抓取单页库存，失败返回 None。"""
    url = _build_url(steam_id, proxy_cfg, start_assetid)
    headers = _build_headers(proxy_cfg)
    for attempt in range(settings.max_retries + 1):
        try:
            response = await client.get(
                url,
                headers=headers,
                timeout=settings.request_timeout_seconds,
            )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                wait = int(retry_after) if str(retry_after).isdigit() else 60
                logger.warning("Steam API 返回 429 (限流)，等待 %ds 后重试 (attempt %d/%d)",
                               wait, attempt + 1, settings.max_retries + 1)
                if attempt < settings.max_retries:
                    await asyncio.sleep(wait)
                    continue
                return None

            if response.status_code == 403:
                logger.warning("Steam API 返回 403 (禁止访问) — 库存可能为私密或 IP 被封锁")
                return None

            if response.status_code == 200:
                try:
                    data = response.json()
                except Exception:
                    logger.warning("Steam API 返回非 JSON 响应 (attempt %d)", attempt + 1)
                    if attempt < settings.max_retries:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return None
                if data and data.get("success"):
                    return data
                if data is None:
                    logger.warning("Steam API 返回 null — 库存可能为私密或需要 steamLoginSecure cookie")
                elif not data.get("success"):
                    logger.warning("Steam API 返回 success=false (数据: %s)",
                                   str(data)[:200])
                return None

            if attempt < settings.max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            return None

        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
        ) as exc:
            logger.warning("网络异常 (attempt %d/%d): %s",
                           attempt + 1, settings.max_retries + 1, exc)
            if attempt < settings.max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            raise

    return None


async def fetch_inventory_paginated(steam_id: str) -> dict[str, Any] | None:
    """分页抓取 CS2 完整库存。

    返回格式: {"assets": [...], "descriptions": [...], "total_items": int}
    库存为空或私密时返回 None。
    """
    all_assets: list[dict[str, Any]] = []
    desc_map: dict[str, dict[str, Any]] = {}
    seen_asset_ids: set[str] = set()

    # 获取代理配置
    proxy_cfg = _get_proxy_config()

    logger.debug("连接方式: proxy=%s, hosts=%s",
                 proxy_cfg.get("proxy"), proxy_cfg.get("hosts_override"))

    client = await get_client()
    start_assetid: str | None = None
    more_pages = True
    page_count = 0
    total_count = 0

    while more_pages:
        data = await _fetch_page(client, steam_id, proxy_cfg, start_assetid)
        if data is None:
            if page_count == 0:
                return None
            break

        assets = data.get("assets", [])
        descriptions = data.get("descriptions", [])

        new_assets = []
        for a in assets:
            aid = a.get("assetid", "")
            if aid and aid not in seen_asset_ids:
                seen_asset_ids.add(aid)
                new_assets.append(a)
        all_assets.extend(new_assets)

        for d in descriptions:
            key = f"{d.get('classid', '')}_{d.get('instanceid', '')}"
            if key not in desc_map:
                desc_map[key] = d

        page_count += 1

        total_count = data.get("total_inventory_count", 0)
        more = data.get("more", False)
        last_assetid = data.get("last_assetid")

        has_more = bool(more)
        if not has_more and last_assetid and len(assets) > 0:
            has_more = True
        if not has_more and last_assetid and total_count > len(all_assets):
            has_more = True

        if has_more and last_assetid:
            start_assetid = last_assetid
            await asyncio.sleep(settings.request_delay_seconds)
        else:
            more_pages = False

    if not all_assets:
        return None

    return {
        "assets": all_assets,
        "descriptions": list(desc_map.values()),
        "total_items": len(all_assets),
        "total_inventory_count": total_count,
    }
