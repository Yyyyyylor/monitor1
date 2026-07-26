"""核心数据模型：Item 数据类、变化类型、变化事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ChangeType(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    SWAPPED = "swapped"


@dataclass
class Item:
    """标准化库存物品。"""

    asset_id: str
    classid: str
    instanceid: str
    market_hash_name: str
    market_name: str | None = None
    icon_url: str | None = None
    rarity: str | None = None
    type_line: str | None = None
    tradable: bool = False
    marketable: bool = False
    # 扩展属性（磨损、印花、阶段等）
    attributes: dict[str, Any] = field(default_factory=dict)
    # Steam 原始 tags
    tags: list[dict[str, Any]] = field(default_factory=list)

    @property
    def identity_key(self) -> tuple[str, str]:
        """用于交换识别的匹配键：classid + instanceid。"""
        return (self.classid, self.instanceid)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "classid": self.classid,
            "instanceid": self.instanceid,
            "market_hash_name": self.market_hash_name,
            "market_name": self.market_name,
            "icon_url": self.icon_url,
            "rarity": self.rarity,
            "type_line": self.type_line,
            "tradable": self.tradable,
            "marketable": self.marketable,
            "attributes": self.attributes,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Item:
        return cls(
            asset_id=data["asset_id"],
            classid=data.get("classid", ""),
            instanceid=data.get("instanceid", ""),
            market_hash_name=data.get("market_hash_name", ""),
            market_name=data.get("market_name"),
            icon_url=data.get("icon_url"),
            rarity=data.get("rarity"),
            type_line=data.get("type_line"),
            tradable=data.get("tradable", False),
            marketable=data.get("marketable", False),
            attributes=data.get("attributes", {}),
            tags=data.get("tags", []),
        )


@dataclass
class ChangeEvent:
    """一次库存变化事件。"""

    steam_id: str
    change_type: ChangeType
    asset_id: str
    old_asset_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    change_time: datetime | None = None
    snapshot_before: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "steam_id": self.steam_id,
            "change_type": self.change_type.value,
            "asset_id": self.asset_id,
            "old_asset_id": self.old_asset_id,
            "detail": self.detail,
            "change_time": self.change_time.isoformat() if self.change_time else None,
            "snapshot_before": self.snapshot_before,
        }


@dataclass
class InventorySnapshot:
    """当前库存基准快照（内存表示）。"""

    steam_id: str
    items: dict[str, Item]  # asset_id -> Item
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    item_count: int = 0
    api_total_count: int = 0  # Steam API total_inventory_count

    @classmethod
    def empty(cls, steam_id: str) -> InventorySnapshot:
        return cls(steam_id=steam_id, items={}, item_count=0, api_total_count=0)
