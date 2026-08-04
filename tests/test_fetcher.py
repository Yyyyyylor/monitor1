"""分页抓取测试 — 任一页失败时必须整体失败，禁止返回不完整库存（回归 H2）。"""

from __future__ import annotations

from unittest.mock import AsyncMock

from httpx import TimeoutException

from src.config import settings
from src.crawler import fetcher


def _make_first_page() -> dict:
    """构造一个还有下一页（more=True）的响应。"""
    return {
        "success": True,
        "assets": [{"assetid": "1", "classid": "1", "instanceid": "1", "amount": "1"}],
        "descriptions": [
            {"classid": "1", "instanceid": "1", "market_hash_name": "AK-47 | Redline"}
        ],
        "total_inventory_count": 100,
        "more": True,
        "last_assetid": "1",
    }


def _patch_deps(mocker) -> None:
    """隔离网络与真实代理检测，仅保留分页循环逻辑。"""
    mocker.patch.object(fetcher, "_get_proxy_config", return_value={
        "proxy": None, "hosts_override": None, "verify": True, "headers_extra": {},
    })

    async def _fake_get_client():
        return object()

    mocker.patch.object(fetcher, "get_client", _fake_get_client)
    mocker.patch.object(settings, "request_delay_seconds", 0)


async def test_mid_page_exception_returns_none(mocker) -> None:
    """第 2 页抛网络异常 → 整体返回 None，而不是部分库存。"""
    _patch_deps(mocker)
    mocker.patch.object(
        fetcher, "_fetch_page",
        AsyncMock(side_effect=[_make_first_page(), TimeoutException("boom")]),
    )

    try:
        result = await fetcher.fetch_inventory_paginated("76561190000000000")
    finally:
        await fetcher.close_client()

    assert result is None


async def test_mid_page_none_returns_none(mocker) -> None:
    """第 2 页返回 None（重试耗尽/限流/私密）→ 整体返回 None，而不是部分库存。"""
    _patch_deps(mocker)
    mocker.patch.object(
        fetcher, "_fetch_page",
        AsyncMock(side_effect=[_make_first_page(), None]),
    )

    try:
        result = await fetcher.fetch_inventory_paginated("76561190000000000")
    finally:
        await fetcher.close_client()

    assert result is None


async def test_success_returns_full_inventory(mocker) -> None:
    """全部页成功（含末页空资产兜底）→ 正常返回完整库存，避免修复误伤正常路径。"""
    _patch_deps(mocker)
    first = _make_first_page()  # assets=[1], more=True, last_assetid="1"
    last_page = {
        "success": True,
        "assets": [{"assetid": "2", "classid": "2", "instanceid": "2", "amount": "1"}],
        "descriptions": [
            {"classid": "2", "instanceid": "2", "market_hash_name": "AWP | Asiimov"}
        ],
        "total_inventory_count": 2,
        "more": False,
        "last_assetid": "2",
    }
    # 末页返回空资产（last_assetid 仍存在），触发原有 has_more 兜底后终止
    empty_page = {
        "success": True,
        "assets": [],
        "descriptions": [],
        "total_inventory_count": 2,
        "more": False,
        "last_assetid": "2",
    }
    mocker.patch.object(
        fetcher, "_fetch_page",
        AsyncMock(side_effect=[first, last_page, empty_page]),
    )

    try:
        result = await fetcher.fetch_inventory_paginated("76561190000000000")
    finally:
        await fetcher.close_client()

    assert result is not None
    assert result["total_items"] == 2
    assert {a["assetid"] for a in result["assets"]} == {"1", "2"}
