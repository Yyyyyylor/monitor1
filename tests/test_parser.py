"""解析引擎测试。"""

from __future__ import annotations

from typing import Any

import pytest

from src.crawler.parser import parse_inventory_response
from src.models.item import Item


def test_parse_valid_inventory(mock_raw_inventory: dict[str, Any]) -> None:
    items = parse_inventory_response(mock_raw_inventory)
    assert items is not None
    assert len(items) == 3
    assert "1001" in items
    assert "1002" in items
    assert "1003" in items


def test_parse_attributes(mock_raw_inventory: dict[str, Any]) -> None:
    items = parse_inventory_response(mock_raw_inventory)
    assert items is not None
    item = items["1001"]
    assert item.market_hash_name == "AK-47 | Redline (Field-Tested)"
    assert item.attributes.get("paint_wear") == 0.15
    assert item.attributes.get("paint_seed") == 588
    assert item.rarity == "Classified"


def test_parse_empty() -> None:
    assert parse_inventory_response(None) is None
    assert parse_inventory_response({"assets": [], "descriptions": []}) is None


def test_parse_no_descriptions() -> None:
    raw = {"assets": [{"assetid": "1", "classid": "1", "instanceid": "1"}], "descriptions": []}
    assert parse_inventory_response(raw) is None


def test_parse_missing_assetid() -> None:
    raw = {
        "assets": [{"classid": "1", "instanceid": "1"}],
        "descriptions": [{"classid": "1", "instanceid": "1", "market_hash_name": "Test Item"}],
    }
    items = parse_inventory_response(raw)
    assert items is not None
    assert len(items) == 0  # assetid missing -> skipped


def test_item_to_dict_roundtrip(sample_item: Item, sample_item_data: dict[str, Any]) -> None:
    d = sample_item.to_dict()
    assert d["asset_id"] == sample_item_data["asset_id"]
    assert d["attributes"]["paint_wear"] == 0.15

    restored = Item.from_dict(d)
    assert restored.asset_id == sample_item.asset_id
    assert restored.market_hash_name == sample_item.market_hash_name
    assert restored.attributes["paint_wear"] == 0.15


def test_parse_app_data_stickers(mock_raw_inventory: dict[str, Any]) -> None:
    """app_data 中的印花应被正确提取（含 sticker_id 和 wear）。"""
    items = parse_inventory_response(mock_raw_inventory)
    assert items is not None
    awp = items["1003"]
    assert awp.market_hash_name == "AWP | Asiimov (Field-Tested)"
    stickers = awp.attributes.get("stickers", [])
    assert len(stickers) == 1
    assert stickers[0]["name"] == "iBUYPOWER (Holo) | Katowice 2014"
    assert stickers[0]["sticker_id"] == "12345"
    assert stickers[0]["wear"] == 0.1
    assert stickers[0]["slot"] == 0


def test_parse_total_count(mock_raw_inventory: dict[str, Any]) -> None:
    """所有物品应被正确解析。"""
    items = parse_inventory_response(mock_raw_inventory)
    assert items is not None
    assert len(items) == 3


def test_parse_html_stickers() -> None:
    """HTML 格式的印花信息应被正确提取。"""
    raw = {
        "assets": [
            {"assetid": "9001", "classid": "900", "instanceid": "901", "amount": "1"},
        ],
        "descriptions": [
            {
                "classid": "900",
                "instanceid": "901",
                "market_hash_name": "AWP | Fade (Factory New)",
                "marketable": True,
                "tradable": True,
                "descriptions": [
                    {"value": "Exterior: Factory New"},
                    {
                        "value": '<br><div id="sticker_info" class="sticker_info" style="border: 2px solid rgb(102, 102, 102);">'
                                 '<center><img width=64 height=48 src="https://cdn.steamstatic.com/sticker.png" '
                                 'title="Sticker: Evil Geniuses (Gold) | Stockholm 2021"><br>'
                                 'Sticker: Evil Geniuses (Gold) | Stockholm 2021</center></div>'
                    },
                ],
                "tags": [],
            },
        ],
    }
    items = parse_inventory_response(raw)
    assert items is not None
    item = items["9001"]
    stickers = item.attributes.get("stickers", [])
    assert len(stickers) >= 1
    assert "Evil Geniuses" in stickers[0]["name"]
