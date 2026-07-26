"""饰品名称汉化 — 基于 CS2 官方语言文件的中英文对照。

market_hash_name 格式示例：
  "AK-47 | Redline (Field-Tested)"
  "★ Karambit | Fade (Factory New)"
  "Sticker | iBUYPOWER (Holo) | Katowice 2014"
  "Sport Gloves | Superconductor (Field-Tested)"

翻译策略：按 " | " 和 "()" 拆分，逐段查表替换。
"""

from __future__ import annotations

import re
from functools import lru_cache

# 延迟加载翻译表（避免导入时就加载大文件）
_trans_map: dict[str, str] | None = None


def _load_map() -> dict[str, str]:
    """加载翻译表 — 优先 JSON，失败则回退到 importlib 动态加载 Python 模块。"""
    global _trans_map
    if _trans_map is not None:
        return _trans_map

    import json
    from pathlib import Path

    # 优先：JSON 文件（加载快、可被打包工具识别）
    candidates_json = [
        Path(__file__).resolve().parent.parent.parent / "translate" / "translation_map.json",
        Path("translate") / "translation_map.json",
    ]
    for p in candidates_json:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                _trans_map = json.load(f)
            return _trans_map

    # 回退：Python 模块（兼容旧版）
    import importlib.util
    candidates_py = [
        Path(__file__).resolve().parent.parent.parent / "translate" / "translation_map.py",
        Path("translate") / "translation_map.py",
    ]
    for p in candidates_py:
        if p.exists():
            spec = importlib.util.spec_from_file_location("translation_map", str(p))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _trans_map = getattr(mod, "TRANSLATION_MAP", {})
                return _trans_map
    _trans_map = {}
    return _trans_map


@lru_cache(maxsize=4096)
def translate_name(name: str) -> str:
    """将 market_hash_name 翻译为中文。

    策略：
      1. 整体查表（如 "★ Karambit | Fade" 这类复合键）
      2. 按 " | " 拆分，逐段查表
      3. 磨损括号 "(Field-Tested)" 单独翻译
      4. 查不到的段保留原文
    """
    if not name:
        return name

    m = _load_map()
    if not m:
        return name

    # 整体命中
    if name in m:
        return m[name]

    # 拆分磨损括号: "AK-47 | Redline (Field-Tested)" → ["AK-47 | Redline", "Field-Tested"]
    wear_match = re.search(r"\(([^)]+)\)\s*$", name)
    wear_en = wear_match.group(1) if wear_match else ""
    base_name = name[:wear_match.start()].strip() if wear_match else name

    # 按 " | " 拆分各段
    parts = base_name.split(" | ")
    translated_parts = []
    for part in parts:
        part = part.strip()
        if part in m:
            translated_parts.append(m[part])
        else:
            # 尝试去掉 ★ 前缀查表
            if part.startswith("★ "):
                inner = part[2:]
                if inner in m:
                    translated_parts.append("★ " + m[inner])
                    continue
            translated_parts.append(part)

    result = " | ".join(translated_parts)

    # 拼接磨损翻译
    if wear_en:
        wear_zh = m.get(wear_en, wear_en)
        result = f"{result} ({wear_zh})"

    return result


def translate_short(name: str) -> str:
    """短翻译：只翻译武器名和磨损，皮肤名保留英文。

    "AK-47 | Redline (Field-Tested)" → "AK-47 | Redline (久经沙场)"
    """
    if not name:
        return name

    m = _load_map()
    if not m:
        return name

    wear_match = re.search(r"\(([^)]+)\)\s*$", name)
    if not wear_match:
        return name

    wear_en = wear_match.group(1)
    base = name[:wear_match.start()].strip()
    wear_zh = m.get(wear_en, wear_en)
    return f"{base} ({wear_zh})"
