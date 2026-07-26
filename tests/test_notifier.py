"""通知消息格式化测试。"""

from __future__ import annotations

from src.models.item import ChangeEvent, ChangeType
from src.notifications.user_notifier import _extract_item_name, _format_change_message


def _make_event(change_type: ChangeType, **kwargs) -> ChangeEvent:
    return ChangeEvent(
        steam_id="test_user",
        change_type=change_type,
        asset_id=kwargs.pop("asset_id", "aid1"),
        old_asset_id=kwargs.pop("old_asset_id", None),
        detail=kwargs.pop("detail", {}),
    )


class TestExtractItemName:
    def test_added(self) -> None:
        ev = _make_event(ChangeType.ADDED, detail={"item": {"market_hash_name": "AK-47 | Redline"}})
        assert _extract_item_name(ev) == "AK-47 | Redline"

    def test_removed(self) -> None:
        ev = _make_event(ChangeType.REMOVED, detail={"item": {"market_hash_name": "Desert Eagle | Blaze"}})
        assert _extract_item_name(ev) == "Desert Eagle | Blaze"

    def test_modified(self) -> None:
        ev = _make_event(ChangeType.MODIFIED, detail={"changes": {}, "current_state": {"market_hash_name": "AWP | Asiimov"}})
        assert _extract_item_name(ev) == "AWP | Asiimov"

    def test_swapped(self) -> None:
        ev = _make_event(ChangeType.SWAPPED, detail={"market_hash_name": "Glock | Fade", "attribute_diffs": {}})
        assert _extract_item_name(ev) == "Glock | Fade"

    def test_unknown_fallback(self) -> None:
        ev = _make_event(ChangeType.ADDED, detail={})
        assert _extract_item_name(ev) == "Unknown Item"


class TestFormatMessage:
    def test_added_message(self) -> None:
        ev = _make_event(ChangeType.ADDED, detail={
            "item": {
                "market_hash_name": "AK-47 | Redline",
                "attributes": {"paint_wear": 0.1523},
            }
        })
        msg = _format_change_message(ev)
        assert "✅ 新增" in msg
        assert "AK-47 | Redline" in msg
        assert "0.1523" in msg

    def test_removed_message(self) -> None:
        ev = _make_event(ChangeType.REMOVED, detail={"item": {"market_hash_name": "Deagle | Blaze"}})
        msg = _format_change_message(ev)
        assert "❌ 移除" in msg
        assert "Deagle | Blaze" in msg

    def test_modified_message(self) -> None:
        ev = _make_event(ChangeType.MODIFIED, detail={
            "changes": {"paint_wear": [0.15, 0.12]},
            "current_state": {"market_hash_name": "AK-47 | Redline"},
        })
        msg = _format_change_message(ev)
        assert "📝 修改" in msg
        assert "AK-47 | Redline" in msg
        assert "0.15" in msg
        assert "0.12" in msg

    def test_swapped_message(self) -> None:
        ev = _make_event(ChangeType.SWAPPED, old_asset_id="old1", asset_id="new1", detail={
            "market_hash_name": "Glock | Fade",
            "attribute_diffs": {"paint_wear": [0.01, 0.02]},
        })
        msg = _format_change_message(ev)
        assert "🔄 交换" in msg
        assert "Glock | Fade" in msg
        assert "old1" in msg
        assert "new1" in msg
