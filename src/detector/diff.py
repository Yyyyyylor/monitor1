"""变更检测器 — 差异比较 + 交换识别 + 批量活动汇总。

优化：使用指纹哈希快速跳过未变化物品，只对指纹不同的物品做深入比较。
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.models.item import ChangeEvent, ChangeType, Item


# ---------------------------------------------------------------------------
# 指纹哈希 — 快速判断物品是否变化
# ---------------------------------------------------------------------------

def _item_fingerprint(item: Item) -> str:
    """计算物品的稳定指纹（MD5 hex 前 16 位）。

    覆盖所有可能变化的字段：market_hash_name, rarity, tradable,
    marketable, attributes, tags。不包含 asset_id（因为交换识别
    需要匹配不同 asset_id 的同类型物品）。
    """
    parts = [
        item.market_hash_name,
        item.rarity or "",
        str(item.tradable),
        str(item.marketable),
        json.dumps(item.attributes, sort_keys=True, default=str),
        json.dumps(item.tags, sort_keys=True, default=str),
    ]
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _compute_item_diff(old: Item, new: Item) -> dict[str, Any]:
    """比较两个物品的属性差异（仅在指纹不同时才调用）。"""
    changes: dict[str, Any] = {}
    for key, new_val in new.attributes.items():
        old_val = old.attributes.get(key)
        if old_val != new_val:
            changes[key] = [old_val, new_val]
    for key, old_val in old.attributes.items():
        if key not in new.attributes:
            changes[key] = [old_val, None]
    return changes


def detect_changes(
    steam_id: str,
    previous_items: dict[str, Item],
    current_items: dict[str, Item],
) -> list[ChangeEvent]:
    """对比新旧库存，生成变化事件列表（含交换识别）。

    优化策略：
      1. asset_id 集合运算 → 快速定位 added / removed / common
      2. common 物品先比较指纹哈希 → 跳过未变化物品
      3. 仅对指纹不同的物品做深入字段比较
      4. added/removed 中按 identity_key 配对识别交换
    """
    prev_ids = set(previous_items.keys())
    curr_ids = set(current_items.keys())

    removed_ids = prev_ids - curr_ids
    added_ids = curr_ids - prev_ids
    common_ids = prev_ids & curr_ids

    events: list[ChangeEvent] = []

    # ----- 1. 持续存在的物品：先比指纹，再深入比较 -----
    for aid in common_ids:
        old_item = previous_items[aid]
        new_item = current_items[aid]
        # 指纹相同 → 跳过（绝大多数情况）
        if _item_fingerprint(old_item) == _item_fingerprint(new_item):
            continue
        # 指纹不同 → 深入比较
        diff = _compute_item_diff(old_item, new_item)
        if diff:
            events.append(ChangeEvent(
                steam_id=steam_id,
                change_type=ChangeType.MODIFIED,
                asset_id=aid,
                detail={
                    "changes": diff,
                    "current_state": new_item.to_dict(),
                },
                snapshot_before=old_item.to_dict(),
            ))

    # ----- 2. 交换识别：在 added/removed 中配对同 classid+instanceid 的物品 -----
    added_by_identity: dict[tuple[str, str], list[tuple[str, Item]]] = {}
    for aid in added_ids:
        item = current_items[aid]
        added_by_identity.setdefault(item.identity_key, []).append((aid, item))

    removed_by_identity: dict[tuple[str, str], list[tuple[str, Item]]] = {}
    for aid in removed_ids:
        item = previous_items[aid]
        removed_by_identity.setdefault(item.identity_key, []).append((aid, item))

    paired_added: set[str] = set()
    paired_removed: set[str] = set()

    for identity_key, added_list in added_by_identity.items():
        if identity_key not in removed_by_identity:
            continue
        removed_list = removed_by_identity[identity_key]

        for i in range(min(len(added_list), len(removed_list))):
            new_aid, new_item = added_list[i]
            old_aid, old_item = removed_list[i]

            # 交换配对：指纹相同视为同物品重分配（无属性变化），否则记录属性差异
            diff = _compute_item_diff(old_item, new_item)

            events.append(ChangeEvent(
                steam_id=steam_id,
                change_type=ChangeType.SWAPPED,
                asset_id=new_aid,
                old_asset_id=old_aid,
                detail={
                    "old_asset_id": old_aid,
                    "new_asset_id": new_aid,
                    "classid": new_item.classid,
                    "instanceid": new_item.instanceid,
                    "market_hash_name": new_item.market_hash_name,
                    "attribute_diffs": diff,
                    "old_item": old_item.to_dict(),
                    "new_item": new_item.to_dict(),
                },
                snapshot_before=old_item.to_dict(),
            ))
            paired_added.add(new_aid)
            paired_removed.add(old_aid)

    # ----- 3. 未配对的 removed -----
    for aid in (removed_ids - paired_removed):
        item = previous_items[aid]
        events.append(ChangeEvent(
            steam_id=steam_id,
            change_type=ChangeType.REMOVED,
            asset_id=aid,
            detail={"item": item.to_dict()},
            snapshot_before=item.to_dict(),
        ))

    # ----- 4. 未配对的 added -----
    for aid in (added_ids - paired_added):
        item = current_items[aid]
        events.append(ChangeEvent(
            steam_id=steam_id,
            change_type=ChangeType.ADDED,
            asset_id=aid,
            detail={"item": item.to_dict()},
        ))

    return events


# ---------------------------------------------------------------------------
# 库存活动分类（基于 total_inventory_count 与实际返回数的 delta）
# ---------------------------------------------------------------------------

@dataclass
class InventoryActivity:
    """基于 API 元数据的库存活动分类。"""

    category: str  # "storage_deposit", "storage_withdrawal", "acquired", "disposed", "mixed", "unchanged"
    total_delta: int = 0    # total_inventory_count 变化量
    returned_delta: int = 0  # api 实际返回数变化量
    detail: dict[str, int] = field(default_factory=dict)  # market_hash_name -> 变化数量（仅批量场景）

    _LABELS = {
        "storage_deposit": "📦 存入存储单元",
        "storage_withdrawal": "📤 从存储单元取出",
        "acquired": "📥 获得新物品",
        "disposed": "📤 移除物品",
        "mixed": "🔄 混合变动",
        "unchanged": "— 无变化",
    }

    def summary_line(self) -> str:
        label = self._LABELS.get(self.category, self.category)
        parts = []
        if self.detail:
            parts = [f"{name} x{cnt}" for name, cnt in sorted(self.detail.items())]
        info = f" ({', '.join(parts)})" if parts else ""
        return f"{label}{info} [total{self.total_delta:+d}, returned{self.returned_delta:+d}]"


def classify_activity(
    prev_total: int,
    prev_returned: int,
    new_total: int,
    new_returned: int,
    added_names: dict[str, int] | None = None,
    removed_names: dict[str, int] | None = None,
) -> InventoryActivity:
    """基于 total_inventory_count 与 api 实际返回数的 delta 分类活动类型。

    逻辑：
      total 不变 + returned 变化 → 存入/取出存储单元
      total 和 returned 同向变化 → 买入/卖出
      其他 → 混合变动
    """
    total_delta = new_total - prev_total
    returned_delta = new_returned - prev_returned

    if total_delta == 0 and returned_delta == 0:
        return InventoryActivity(category="unchanged")

    if total_delta == 0 and returned_delta != 0:
        # total 不动，returned 变 → 存储单元操作
        category = "storage_withdrawal" if returned_delta > 0 else "storage_deposit"
        detail = added_names if returned_delta > 0 else removed_names
        return InventoryActivity(
            category=category,
            total_delta=total_delta,
            returned_delta=returned_delta,
            detail=detail or {},
        )

    if total_delta > 0 and returned_delta >= 0:
        # 两者都增加 → 获得新物品
        return InventoryActivity(
            category="acquired",
            total_delta=total_delta,
            returned_delta=returned_delta,
            detail=added_names or {},
        )

    if total_delta < 0 and returned_delta <= 0:
        # 两者都减少 → 移除物品
        return InventoryActivity(
            category="disposed",
            total_delta=total_delta,
            returned_delta=returned_delta,
            detail=removed_names or {},
        )

    # 其他情况（方向不一致）→ 混合变动
    detail = {}
    if added_names:
        detail.update({f"+{k}": v for k, v in added_names.items()})
    if removed_names:
        detail.update({f"-{k}": v for k, v in removed_names.items()})
    return InventoryActivity(
        category="mixed",
        total_delta=total_delta,
        returned_delta=returned_delta,
        detail=detail,
    )


def analyze_activity(
    events: list[ChangeEvent],
    prev_total: int,
    prev_returned: int,
    new_total: int,
    new_returned: int,
) -> InventoryActivity:
    """综合分析：从事件中提取增减明细，结合 delta 分类。"""
    added_names: dict[str, int] = defaultdict(int)
    removed_names: dict[str, int] = defaultdict(int)

    for ev in events:
        if ev.change_type == ChangeType.ADDED:
            name = ev.detail.get("item", {}).get("market_hash_name", "Unknown")
            added_names[name] += 1
        elif ev.change_type == ChangeType.REMOVED:
            name = ev.detail.get("item", {}).get("market_hash_name", "Unknown")
            removed_names[name] += 1

    return classify_activity(
        prev_total=prev_total,
        prev_returned=prev_returned,
        new_total=new_total,
        new_returned=new_returned,
        added_names=dict(added_names) if added_names else None,
        removed_names=dict(removed_names) if removed_names else None,
    )

