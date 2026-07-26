"""变更检测器测试 — 新增、移除、修改、交换识别。"""

from __future__ import annotations

from src.detector.diff import detect_changes
from src.models.item import ChangeType, Item


def _make_item(
    asset_id: str,
    classid: str = "1",
    instanceid: str = "1",
    name: str = "Test Item",
    wear: float | None = None,
    stattrak: int | None = None,
) -> Item:
    attrs = {}
    if wear is not None:
        attrs["paint_wear"] = wear
    if stattrak is not None:
        attrs["stattrak_count"] = stattrak
    return Item(
        asset_id=asset_id,
        classid=classid,
        instanceid=instanceid,
        market_hash_name=name,
        attributes=attrs,
    )


class TestAddRemove:
    def test_add_one_item(self) -> None:
        prev = {}
        curr = {"a1": _make_item("a1", name="AK-47")}
        events = detect_changes("test", prev, curr)
        assert len(events) == 1
        assert events[0].change_type == ChangeType.ADDED
        assert events[0].asset_id == "a1"

    def test_remove_one_item(self) -> None:
        prev = {"a1": _make_item("a1", name="AK-47")}
        curr = {}
        events = detect_changes("test", prev, curr)
        assert len(events) == 1
        assert events[0].change_type == ChangeType.REMOVED
        assert events[0].asset_id == "a1"

    def test_no_changes(self) -> None:
        items = {"a1": _make_item("a1")}
        events = detect_changes("test", items, items)
        assert len(events) == 0

    def test_fingerprint_skips_unchanged(self) -> None:
        """指纹优化：大量相同物品应快速跳过，不产生事件。"""
        # 构造 1000 个相同物品
        prev = {f"a{i}": _make_item(f"a{i}", name="AK-47", wear=0.15) for i in range(1000)}
        curr = {f"a{i}": _make_item(f"a{i}", name="AK-47", wear=0.15) for i in range(1000)}
        events = detect_changes("test", prev, curr)
        assert len(events) == 0

    def test_fingerprint_detects_change(self) -> None:
        """指纹优化：磨损变化应被检测到。"""
        prev = {f"a{i}": _make_item(f"a{i}", name="AK-47", wear=0.15) for i in range(100)}
        curr = {f"a{i}": _make_item(f"a{i}", name="AK-47", wear=0.15) for i in range(100)}
        # 只改第 50 个
        curr["a50"] = _make_item("a50", name="AK-47", wear=0.99)
        events = detect_changes("test", prev, curr)
        assert len(events) == 1
        assert events[0].change_type == ChangeType.MODIFIED
        assert events[0].asset_id == "a50"

    def test_add_and_remove(self) -> None:
        """不同 classid 的物品应该分别识别为 remove + add，不会被配对为 swap。"""
        prev = {"a1": _make_item("a1", classid="10", instanceid="10", name="Old")}
        curr = {"a2": _make_item("a2", classid="20", instanceid="20", name="New")}
        events = detect_changes("test", prev, curr)
        assert len(events) == 2
        types = {e.change_type for e in events}
        assert types == {ChangeType.ADDED, ChangeType.REMOVED}


class TestModification:
    def test_wear_changed(self) -> None:
        prev = {"a1": _make_item("a1", name="AK-47", wear=0.15)}
        curr = {"a1": _make_item("a1", name="AK-47", wear=0.12)}
        events = detect_changes("test", prev, curr)
        assert len(events) == 1
        assert events[0].change_type == ChangeType.MODIFIED
        assert events[0].asset_id == "a1"
        changes = events[0].detail["changes"]
        assert "paint_wear" in changes
        assert changes["paint_wear"] == [0.15, 0.12]

    def test_stattrak_changed(self) -> None:
        prev = {"a1": _make_item("a1", name="AK-47", stattrak=10)}
        curr = {"a1": _make_item("a1", name="AK-47", stattrak=15)}
        events = detect_changes("test", prev, curr)
        assert len(events) == 1
        assert events[0].change_type == ChangeType.MODIFIED
        assert events[0].detail["changes"]["stattrak_count"] == [10, 15]


class TestSwapDetection:
    def test_simple_swap(self) -> None:
        """相同 classid/instanceid 但不同 asset_id → swapped"""
        prev = {"old_aid": _make_item("old_aid", classid="1", instanceid="1", name="AK-47", wear=0.15)}
        curr = {"new_aid": _make_item("new_aid", classid="1", instanceid="1", name="AK-47", wear=0.16)}
        events = detect_changes("test", prev, curr)
        assert len(events) == 1
        assert events[0].change_type == ChangeType.SWAPPED
        assert events[0].asset_id == "new_aid"
        assert events[0].old_asset_id == "old_aid"
        assert events[0].detail["attribute_diffs"]["paint_wear"] == [0.15, 0.16]

    def test_swap_with_real_add_remove(self) -> None:
        """一个 swap + 一个真正的 add + 一个真正的 remove"""
        prev = {
            "old_aid": _make_item("old_aid", classid="1", instanceid="1", name="AK-47"),
            "removed_aid": _make_item("removed_aid", classid="2", instanceid="2", name="Deagle"),
        }
        curr = {
            "new_aid": _make_item("new_aid", classid="1", instanceid="1", name="AK-47"),
            "added_aid": _make_item("added_aid", classid="3", instanceid="3", name="AWP"),
        }
        events = detect_changes("test", prev, curr)
        assert len(events) == 3
        types = {e.change_type for e in events}
        assert types == {ChangeType.SWAPPED, ChangeType.ADDED, ChangeType.REMOVED}

    def test_no_swap_when_different_class(self) -> None:
        """不同 classid 不应配对为 swap"""
        prev = {"a1": _make_item("a1", classid="1", name="AK-47")}
        curr = {"a2": _make_item("a2", classid="2", name="AWP")}
        events = detect_changes("test", prev, curr)
        assert len(events) == 2
        assert all(e.change_type != ChangeType.SWAPPED for e in events)

    def test_multiple_swaps(self) -> None:
        """多个相同物品同时交换"""
        prev = {
            "old1": _make_item("old1", classid="1", instanceid="1", name="AK-47", wear=0.15),
            "old2": _make_item("old2", classid="1", instanceid="2", name="M4A4", wear=0.20),
        }
        curr = {
            "new1": _make_item("new1", classid="1", instanceid="1", name="AK-47", wear=0.16),
            "new2": _make_item("new2", classid="1", instanceid="2", name="M4A4", wear=0.21),
        }
        events = detect_changes("test", prev, curr)
        assert len(events) == 2
        assert all(e.change_type == ChangeType.SWAPPED for e in events)


class TestStorageActivity:
    def test_storage_deposit(self) -> None:
        """total 不变 + returned 减少 → 存入存储单元"""
        from src.detector import classify_activity

        result = classify_activity(
            prev_total=100, prev_returned=80,
            new_total=100, new_returned=75,
            removed_names={"AK-47 | Redline": 5},
        )
        assert result.category == "storage_deposit"
        assert result.total_delta == 0
        assert result.returned_delta == -5
        assert "存入存储单元" in result.summary_line()

    def test_storage_withdrawal(self) -> None:
        """total 不变 + returned 增加 → 从存储单元取出"""
        from src.detector import classify_activity

        result = classify_activity(
            prev_total=100, prev_returned=80,
            new_total=100, new_returned=83,
            added_names={"AWP | Fade": 3},
        )
        assert result.category == "storage_withdrawal"
        assert result.total_delta == 0
        assert result.returned_delta == 3
        assert "从存储单元取出" in result.summary_line()

    def test_acquired(self) -> None:
        """total 和 returned 都增加 → 获得新物品"""
        from src.detector import classify_activity

        result = classify_activity(
            prev_total=100, prev_returned=80,
            new_total=103, new_returned=82,
            added_names={"MP7 | Fade": 2},
        )
        assert result.category == "acquired"
        assert result.total_delta == 3
        assert result.returned_delta == 2

    def test_disposed(self) -> None:
        """total 和 returned 都减少 → 移除物品"""
        from src.detector import classify_activity

        result = classify_activity(
            prev_total=100, prev_returned=80,
            new_total=97, new_returned=78,
            removed_names={"AK-47 | Redline": 2},
        )
        assert result.category == "disposed"
        assert result.total_delta == -3
        assert result.returned_delta == -2

    def test_unchanged(self) -> None:
        """无变化"""
        from src.detector import classify_activity

        result = classify_activity(
            prev_total=100, prev_returned=80,
            new_total=100, new_returned=80,
        )
        assert result.category == "unchanged"

    def test_mixed(self) -> None:
        """total 增加但 returned 减少 → 混合变动"""
        from src.detector import classify_activity

        result = classify_activity(
            prev_total=100, prev_returned=80,
            new_total=102, new_returned=79,
            added_names={"New Item": 1},
            removed_names={"Old Item": 1},
        )
        assert result.category == "mixed"
