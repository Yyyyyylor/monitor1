"""健壮的属性提取器 — 将 Steam API 响应解析为标准 Item 对象。"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.models.item import Item

logger = logging.getLogger(__name__)

# 属性前缀识别模式（应对 Steam 多语言描述）
WEAR_PATTERNS = [
    re.compile(r"(?:wear\s*(?:rating|level)?)\s*:?\s*([0-9]+\.[0-9]+)", re.I),
    re.compile(r"(?:磨损|磨損)?\s*:?\s*([0-9]+\.[0-9]+)"),
]

SEED_PATTERNS = [
    re.compile(r"Paint\s*Seed\s*:?\s*(\d+)", re.I),
]

PHASE_PATTERNS = [
    re.compile(r"(?:phase|阶段|階段)\s*:?\s*(Ruby|Sapphire|Emerald|Black\s*Pearl|Phase\s*[1-4])", re.I),
]

STATTRAK_PATTERNS = [
    re.compile(r"StatTrak", re.I),
]

STICKER_SLOT_PATTERNS = [
    re.compile(r"(?:sticker|贴纸|貼紙|印花)\s*(?::?\s*)(.+)", re.I),
]

# HTML 格式的印花信息（Steam 有时用 HTML div 展示印花）
STICKER_HTML_PATTERN = re.compile(
    r"title=\"(Sticker:\s*[^\"]+)\"", re.I
)
STICKER_HTML_TEXT_PATTERN = re.compile(
    r"Sticker:\s*(.+?)(?:<br|</div|$)", re.I | re.S
)


def _extract_from_descriptions(descriptions: list[dict[str, Any]]) -> dict[str, Any]:
    """从物品的 descriptions 数组中提取扩展属性。"""
    attrs: dict[str, Any] = {}

    for desc in descriptions:
        value = desc.get("value", "")
        if not value:
            continue

        # 磨损值
        for pattern in WEAR_PATTERNS:
            m = pattern.search(value)
            if m:
                attrs["paint_wear"] = float(m.group(1))
                break

        # 图案种子
        for pattern in SEED_PATTERNS:
            m = pattern.search(value)
            if m:
                attrs["paint_seed"] = int(m.group(1))
                break

        # 阶段
        for pattern in PHASE_PATTERNS:
            m = pattern.search(value)
            if m:
                raw = m.group(1)
                if raw.lower().startswith("phase"):
                    attrs["phase"] = int(raw[-1])
                else:
                    attrs["phase"] = raw
                break

        # StatTrak
        for pattern in STATTRAK_PATTERNS:
            if pattern.search(value):
                # 尝试从中提取计数
                nums = re.findall(r"\d+", value)
                attrs["stattrak_count"] = int(nums[0]) if nums else 0
                break

    return attrs


def _extract_stickers_from_app_data(descriptions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 descriptions 的 app_data 中提取印花信息（含刮损 wear）。"""
    stickers: list[dict[str, Any]] = []
    for desc in descriptions:
        app_data = desc.get("app_data")
        if not isinstance(app_data, dict):
            continue
        info_list = app_data.get("info")
        if not isinstance(info_list, list):
            continue
        for entry in info_list:
            if not isinstance(entry, dict):
                continue
            sticker_name = entry.get("name", "")
            if not sticker_name:
                continue
            stickers.append({
                "name": sticker_name,
                "sticker_id": str(entry.get("sticker_id", "")),
                "slot": int(entry.get("slot", 0)),
                "wear": float(entry.get("wear", 0)),
            })
    return stickers


def _extract_stickers_from_text(descriptions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 descriptions 文本/HTML 中解析印花名称列表（兜底方案）。"""
    stickers: list[dict[str, Any]] = []

    for desc in descriptions:
        value = desc.get("value", "")
        if not value:
            continue

        # 优先：HTML 格式的印花（<div id="sticker_info">...Sticker: xxx...</div>）
        if "sticker_info" in value.lower() or "title=\"Sticker" in value:
            # 从 title 属性提取（最准确）
            for m in STICKER_HTML_PATTERN.finditer(value):
                name = m.group(1).strip()
                if name:
                    stickers.append({"name": name, "slot": len(stickers), "wear": 0.0})
            # 如果 title 没匹配到，从文本提取
            if not stickers:
                m = STICKER_HTML_TEXT_PATTERN.search(value)
                if m:
                    raw = m.group(1).strip()
                    parts = [p.strip() for p in raw.split(",") if p.strip()]
                    for idx, part in enumerate(parts):
                        stickers.append({"name": part, "slot": idx, "wear": 0.0})
            if stickers:
                break

    if stickers:
        return stickers

    # 兜底：纯文本格式 "Sticker: name1, name2, ..."
    for desc in descriptions:
        value = desc.get("value", "")
        if not value:
            continue
        m = STICKER_SLOT_PATTERNS[0].search(value)
        if m:
            raw = m.group(1).strip()
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            for idx, part in enumerate(parts):
                stickers.append({"name": part, "slot": idx, "wear": 0.0})
            break

    return stickers


def _extract_stickers(tags: list[dict[str, Any]], descriptions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """综合提取印花信息：优先 app_data > tags > 文本兜底。"""
    # 1. 最优来源：app_data（含精确的 sticker_id 和刮损 wear）
    stickers = _extract_stickers_from_app_data(descriptions)
    if stickers:
        return stickers

    # 2. tags 中的 Sticker 分类
    stickers = []
    for tag in tags:
        category = tag.get("category", "")
        if category == "Sticker":
            stickers.append({
                "name": tag.get("localized_tag_name", tag.get("name", "")),
                "slot": tag.get("slot", 0),
                "wear": float(tag.get("wear", 0)),
            })
    if stickers:
        return stickers

    # 3. 兜底：从描述文本解析
    return _extract_stickers_from_text(descriptions)


def _extract_rarity(tags: list[dict[str, Any]]) -> str | None:
    for tag in tags:
        if tag.get("category") == "Rarity":
            return tag.get("localized_tag_name") or tag.get("name")
    return None


def _build_item(
    asset: dict[str, Any], desc: dict[str, Any]
) -> Item:
    """根据一个 asset + 对应 description 构建 Item。"""
    tags = desc.get("tags", [])
    descriptions_raw = desc.get("descriptions", [])

    attributes = _extract_from_descriptions(descriptions_raw)
    stickers = _extract_stickers(tags, descriptions_raw)
    if stickers:
        attributes["stickers"] = stickers

    return Item(
        asset_id=str(asset.get("assetid", "")),
        classid=str(desc.get("classid", "")),
        instanceid=str(desc.get("instanceid", "")),
        market_hash_name=desc.get("market_hash_name", ""),
        market_name=desc.get("market_name"),
        icon_url=desc.get("icon_url"),
        rarity=_extract_rarity(tags),
        type_line=desc.get("type"),
        tradable=bool(asset.get("tradable", False)),
        marketable=bool(desc.get("marketable", False)),
        attributes=attributes,
        tags=tags,
    )


def parse_inventory_response(
    raw: dict[str, Any] | None,
) -> dict[str, Item] | None:
    """将 Steam API 原始响应解析为 {asset_id: Item} 字典。

    返回 None 表示数据无效或为空。
    """
    if raw is None:
        return None

    assets = raw.get("assets", [])
    descriptions = raw.get("descriptions", [])

    if not assets or not descriptions:
        return None

    # 建立 description 索引: classid_instanceid -> description
    desc_index: dict[str, dict[str, Any]] = {}
    for d in descriptions:
        key = f"{d.get('classid', '')}_{d.get('instanceid', '')}"
        desc_index[key] = d

    items: dict[str, Item] = {}
    for asset in assets:
        classid = str(asset.get("classid", ""))
        instanceid = str(asset.get("instanceid", ""))
        asset_id = str(asset.get("assetid", ""))
        if not asset_id:
            continue

        key = f"{classid}_{instanceid}"
        desc = desc_index.get(key)
        if desc is None:
            continue

        item = _build_item(asset, desc)
        items[asset_id] = item

    return items
