"""快照序列化确定性测试（回归 P2）。"""

from __future__ import annotations

from src.db.repository import _serialize_items
from src.models.item import Item


def _item(aid: str, name: str) -> Item:
    return Item(asset_id=aid, classid="c", instanceid="i", market_hash_name=name)


def test_serialize_items_sorted_by_asset_id() -> None:
    items = {"2": _item("2", "B"), "1": _item("1", "A")}
    data = _serialize_items(items)
    assert list(data.keys()) == ["1", "2"]


def test_serialize_items_deterministic() -> None:
    items = {"2": _item("2", "B"), "1": _item("1", "A")}
    reversed_items = {aid: items[aid] for aid in reversed(list(items))}
    assert _serialize_items(items) == _serialize_items(reversed_items)
