"""Web 应用 — 库存监控可视化仪表盘 + API + WebSocket。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from src.config import settings
from src.db.database import async_session_factory, init_db
from src.db.models import CurrentInventoryState, MonitoredUser, SnapshotArchive
from src.db.repository import (
    get_active_users,
    get_recent_changes,
    load_current_snapshot,
    upsert_user,
)
from src.models.item import ChangeEvent
from src.utils import iso_utc as _iso_utc


def _fmt_archive_date(dt) -> str:
    """格式化为 YYMMDDHH（如 26060615）。"""
    if dt is None:
        return ""
    if hasattr(dt, 'hour'):
        return dt.strftime("%y%m%d%H")
    return dt.strftime("%y%m%d") + "00"

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# 服务端密钥与常量
# ---------------------------------------------------------------------------

TOKEN_TTL = 86400  # 24 小时
_TOKEN_SECRET = secrets.token_hex(32)  # 每次重启随机生成，旧 token 失效
_HEARTBEAT_TIMEOUT = 15  # 心跳超时秒数

# ---------------------------------------------------------------------------
# 全局应用状态（封装为数据类，替代散落的模块级 global 变量）
# ---------------------------------------------------------------------------


@dataclass
class WebState:
    """Web 服务运行时状态，集中管理避免 global 分散。"""
    monitor_task: asyncio.Task | None = None
    monitor_running: bool = False
    last_run_time: str = ""
    last_run_stats: dict[str, Any] = field(default_factory=dict)
    last_heartbeat: float = 0.0
    ws_clients: set[web.WebSocketResponse] = field(default_factory=set)
    ws_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    scheduler_ref: Any = None
    heartbeat_event: asyncio.Event = field(default_factory=asyncio.Event)
    # 分层调度状态
    tier_tasks: dict[str, asyncio.Task | None] = field(default_factory=lambda: {"high": None, "medium": None, "low": None})
    tier_events: dict[str, asyncio.Event] = field(default_factory=lambda: {
        "high": asyncio.Event(), "medium": asyncio.Event(), "low": asyncio.Event()
    })
    web_password_initialized: bool = False


_state = WebState()

# ---------------------------------------------------------------------------
# 认证工具
# ---------------------------------------------------------------------------

def _init_web_password() -> str:
    """启动时一次性初始化 Web 密码，空则自动生成。"""
    pwd = settings.web_password
    if not pwd:
        pwd = secrets.token_hex(8)
        settings.web_password = pwd
        logger.warning("=" * 60)
        logger.warning("未配置 WEB_PASSWORD，已自动生成随机密码:")
        logger.warning("  %s", pwd)
        logger.warning("请妥善保存，或编辑 .env 中的 WEB_PASSWORD。")
        logger.warning("=" * 60)
    _state.web_password_initialized = True
    return pwd


def _get_web_password() -> str:
    """获取已初始化的 Web 密码。"""
    if not _state.web_password_initialized:
        return _init_web_password()
    return settings.web_password


def _hash_password(password: str) -> str:
    """对密码做 SHA-256 哈希，避免明文参与 HMAC。"""
    return hashlib.sha256(password.encode()).hexdigest()


def _make_token(password: str) -> str:
    """使用 HMAC-SHA256 生成完整认证 token（密码 hash + 过期时间）。"""
    expires = int(time.time()) + TOKEN_TTL
    payload = f"{expires}"
    pwd_hash = _hash_password(password)
    sig = hmac.new(
        _TOKEN_SECRET.encode(),
        f"{pwd_hash}:{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{expires}.{sig}"


def _verify_token(token: str) -> bool:
    """验证 token 是否有效（恒定时间比较）。"""
    password = _get_web_password()
    if not password:
        return False
    try:
        parts = token.split(".", 1)
        expires = int(parts[0])
        sig = parts[1]
        if time.time() > expires:
            return False
        pwd_hash = _hash_password(password)
        expected = hmac.new(
            _TOKEN_SECRET.encode(),
            f"{pwd_hash}:{expires}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


def _check_auth(request: web.Request) -> bool:
    """检查请求是否已认证。"""
    # Cookie
    token = request.cookies.get("auth_token", "")
    if token and _verify_token(token):
        return True
    # Header
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and _verify_token(auth[7:]):
        return True
    return False


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """认证中间件 — 除登录和静态页面外，所有 API 需要认证。"""
    path = request.path
    # 放行：登录接口、健康检查、首页（会被前端 JS 检查）
    if path in ("/api/login", "/api/status", "/ping", "/health"):
        return await handler(request)
    # 放行：GET / 和 /index.html（前端会自行检查登录状态）
    if request.method == "GET" and path in ("/", "/index.html", "/static/index.html"):
        return await handler(request)
    # API 需要认证
    if path.startswith("/api/"):
        if not _check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


# ---------------------------------------------------------------------------
# 页面路由
# ---------------------------------------------------------------------------

async def index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# API: 监控状态
# ---------------------------------------------------------------------------

async def api_status(request: web.Request) -> web.Response:
    return web.json_response({
        "monitor_running": _state.monitor_running,
        "last_run_time": _state.last_run_time,
        "last_run_stats": _state.last_run_stats,
        "fetch_interval_minutes": settings.fetch_interval_minutes,
        "tiered_scheduling_enabled": settings.tiered_scheduling_enabled,
        "user_count": len(await get_active_users()),
    })


async def api_heartbeat(request: web.Request) -> web.Response:
    """前端心跳 — 维持连接活性，不再触发自动停止。"""
    _state.last_heartbeat = time.monotonic()
    return web.json_response({"ok": True})


# 登录限速常量
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 300  # 5 分钟


async def api_login(request: web.Request) -> web.Response:
    """登录接口 — 验证密码，返回 token（含速率限制，持久化到数据库）。"""
    from src.db.repository import (
        get_login_rate_limit,
        update_login_rate_limit,
        clear_login_rate_limit,
    )

    # 速率限制（从数据库读取，重启不丢失）
    client_ip = request.remote or "127.0.0.1"
    now_ts = time.time()
    attempts, lock_until = await get_login_rate_limit(client_ip)
    if lock_until > now_ts:
        remaining = int(lock_until - now_ts)
        return web.json_response(
            {"error": f"登录尝试过于频繁，请 {remaining} 秒后重试"}, status=429
        )

    try:
        data = await request.json()
    except Exception:
        data = {}
    password = data.get("password", "")
    web_pwd = _get_web_password()

    # 使用常量时间比较防止时序攻击
    if web_pwd and hmac.compare_digest(password, web_pwd):
        await clear_login_rate_limit(client_ip)
        token = _make_token(password)
        resp = web.json_response({"ok": True, "token": token})
        resp.set_cookie(
            "auth_token", token,
            max_age=TOKEN_TTL,
            httponly=True,
            secure=True,
            samesite="Strict",
            path="/",
        )
        return resp

    # 记录失败（持久化到数据库）
    attempts += 1
    lock_until_dt = None
    if attempts >= _LOGIN_MAX_ATTEMPTS:
        from datetime import datetime, timezone, timedelta
        lock_until_dt = datetime.now(timezone.utc) + timedelta(seconds=_LOGIN_LOCKOUT_SECONDS)
    await update_login_rate_limit(client_ip, attempts, lock_until_dt)
    logger.warning("登录失败 (IP: %s, 第 %d 次)", client_ip, attempts)
    return web.json_response({"error": "wrong password"}, status=403)


# ---------------------------------------------------------------------------
# API: 用户管理
# ---------------------------------------------------------------------------

async def api_users_list(request: web.Request) -> web.Response:
    """获取活跃用户列表（不含回收站）。单次查询替代 N+1。"""
    from sqlalchemy import select
    async with async_session_factory() as session:
        # 一次查询拿所有用户
        user_result = await session.execute(
            select(MonitoredUser)
            .where(MonitoredUser.deleted_at.is_(None))
            .order_by(MonitoredUser.created_at)
        )
        users = list(user_result.scalars().all())

        # 一次查询拿所有库存快照
        snap_result = await session.execute(select(CurrentInventoryState))
        snap_map = {r.steam_id: r for r in snap_result.scalars().all()}

    items = []
    for u in users:
        snap = snap_map.get(u.steam_id)
        items.append({
            "steam_id": u.steam_id,
            "nickname": u.nickname,
            "is_active": u.is_active,
            "consecutive_fails": u.consecutive_fails,
            "last_error_msg": u.last_error_msg,
            "item_count": snap.item_count if snap else 0,
            "api_total_count": snap.api_total_count if snap else 0,
            "updated_at": _iso_utc(snap.updated_at) if snap else None,
            "created_at": _iso_utc(u.created_at),
            "monitor_frequency": getattr(u, "monitor_frequency", "medium") or "medium",
        })
    return web.json_response(items)


STEAM_URL_RE = re.compile(r"steamcommunity\.com/profiles/(\d{17})")
STEAM_VANITY_RE = re.compile(r"steamcommunity\.com/id/([^/]+)")
STEAMDT_URL_RE = re.compile(r"steamdt\.com/inventory/([a-f0-9]{32})", re.I)


def _extract_steam_id(raw: str) -> str | None:
    """从各种格式中提取 Steam64 ID。

    支持：
      - 纯数字: 76561198768833849
      - Steam 个人资料 URL: https://steamcommunity.com/profiles/76561198768833849
      - Steam 自定义 URL: https://steamcommunity.com/id/xxxxx（无法自动解析）
      - SteamDT URL: https://steamdt.com/inventory/xxxx
    """
    raw = raw.strip()
    if not raw:
        return None

    # 纯数字 17 位，且以 7656119 开头（Steam64 ID 规范）
    if re.fullmatch(r"\d{17}", raw):
        if raw.startswith("7656119"):
            return raw
        return None  # 17位数字但不是合法 Steam64 ID

    # profiles URL
    m = STEAM_URL_RE.search(raw)
    if m:
        return m.group(1)

    # id URL（自定义短链接，无法自动解析）
    if STEAM_VANITY_RE.search(raw):
        return None  # 需要 Steam API 解析，暂不支持

    # 数字 ID 混在其他文本中
    m2 = re.search(r"(\d{17})", raw)
    if m2:
        return m2.group(1)

    return None


async def api_user_add(request: web.Request) -> web.Response:
    data = await request.json()
    raw_input = data.get("steam_id", "").strip()
    nickname = data.get("nickname", "").strip() or None

    steam_id = _extract_steam_id(raw_input)

    # 尝试 SteamDT URL 解析
    if not steam_id and "steamdt.com" in raw_input.lower():
        dt_match = STEAMDT_URL_RE.search(raw_input)
        if dt_match:
            from src.crawler.steamdt import resolve_steamdt_url
            resolved = await resolve_steamdt_url(raw_input)
            if resolved:
                steam_id = resolved
                if not nickname:
                    nickname = f"SteamDT:{dt_match.group(1)[:8]}"

    if not steam_id:
        if re.fullmatch(r"\d{17}", raw_input):
            return web.json_response({"error": f"无效的 Steam ID: {raw_input}（需以 7656119 开头）"}, status=400)
        return web.json_response({"error": f"无法识别: {raw_input}", "hint": "支持: 17位数字ID / steamcommunity.com/profiles/xxx"}, status=400)

    user = await upsert_user(steam_id, nickname)
    return web.json_response({"ok": True, "steam_id": user.steam_id})


async def api_users_batch(request: web.Request) -> web.Response:
    """批量导入用户。

    支持多种格式（每行一条）：
      <id/url>, <备注>
      <备注>, <id/url>
      <id/url>; <备注>
      <备注>; <id/url>
      <id/url>                     （无备注）
    """
    data = await request.json()
    text = data.get("text", "").strip()
    if not text:
        return web.json_response({"error": "请输入内容"}, status=400)

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) > 100:
        return web.json_response({"error": "单次最多导入 100 条"}, status=400)

    results = []
    seen_ids: set[str] = set()

    for i, line in enumerate(lines, 1):
        steam_id, nickname = _parse_batch_line(line)

        if not steam_id:
            results.append({"line": i, "input": line, "ok": False, "error": "无法识别 Steam ID"})
            continue

        if steam_id in seen_ids:
            results.append({"line": i, "input": line, "steam_id": steam_id, "ok": False, "error": "本批次中重复"})
            continue
        seen_ids.add(steam_id)

        try:
            user = await upsert_user(steam_id, nickname)
            results.append({"line": i, "input": line, "steam_id": steam_id, "nickname": nickname, "ok": True, "msg": "已添加"})
        except Exception as e:
            results.append({"line": i, "input": line, "steam_id": steam_id, "ok": False, "error": str(e)[:100]})

    success = sum(1 for r in results if r["ok"])
    fail = len(results) - success
    return web.json_response({"ok": True, "total": len(results), "success": success, "fail": fail, "results": results})


def _parse_batch_line(line: str) -> tuple[str | None, str | None]:
    """解析一行批量导入数据，返回 (steam_id, nickname)。

    支持分隔符：逗号、分号、制表符、竖线
    自动判断哪部分是 ID，哪部分是备注。
    """
    # 按分隔符拆分（空格放在第一位，因为它是最常见的分隔方式）
    for sep in [" ", ";", ",", "\t", "|"]:
        if sep in line:
            parts = [p.strip() for p in line.split(sep, 1)]
            part_a, part_b = parts[0], (parts[1] if len(parts) > 1 else "").strip()

            # 忽略空昵称
            if not part_b:
                part_b = ""

            id_a = _extract_steam_id(part_a)
            id_b = _extract_steam_id(part_b) if part_b else None

            if id_a and id_b:
                return id_a, part_b if part_b != id_b else None
            elif id_a:
                return id_a, part_b or None
            elif id_b:
                return id_b, part_a or None
            else:
                return None, None

    # 无分隔符 → 整行当作 ID
    return _extract_steam_id(line), None


async def api_user_update(request: web.Request) -> web.Response:
    steam_id = request.match_info["steam_id"]
    data = await request.json()
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(MonitoredUser).where(MonitoredUser.steam_id == steam_id))
        user = result.scalar_one_or_none()
        if not user:
            return web.json_response({"error": "not found"}, status=404)
        if "nickname" in data:
            user.nickname = data["nickname"] or None
        if "is_active" in data:
            user.is_active = bool(data["is_active"])
        await session.commit()
    return web.json_response({"ok": True})


async def api_user_delete(request: web.Request) -> web.Response:
    """软删除：将用户移入回收站（保留所有数据）。"""
    steam_id = request.match_info["steam_id"]
    from src.db.repository import soft_delete_user
    ok = await soft_delete_user(steam_id)
    if not ok:
        return web.json_response({"error": "用户不存在"}, status=404)
    return web.json_response({"ok": True, "msg": "已移入回收站"})


async def api_trash_list(request: web.Request) -> web.Response:
    """获取回收站中的用户列表。"""
    from sqlalchemy import select
    async with async_session_factory() as session:
        user_result = await session.execute(
            select(MonitoredUser)
            .where(MonitoredUser.deleted_at.is_not(None))
            .order_by(MonitoredUser.deleted_at.desc())
        )
        users = list(user_result.scalars().all())

        snap_result = await session.execute(select(CurrentInventoryState))
        snap_map = {r.steam_id: r for r in snap_result.scalars().all()}

    items = []
    for u in users:
        snap = snap_map.get(u.steam_id)
        items.append({
            "steam_id": u.steam_id,
            "nickname": u.nickname,
            "is_active": u.is_active,
            "item_count": snap.item_count if snap else 0,
            "deleted_at": _iso_utc(u.deleted_at),
        })
    return web.json_response(items)


async def api_user_restore(request: web.Request) -> web.Response:
    """从回收站恢复用户。"""
    steam_id = request.match_info["steam_id"]
    from src.db.repository import restore_user
    ok = await restore_user(steam_id)
    if not ok:
        return web.json_response({"error": "用户不存在"}, status=404)
    return web.json_response({"ok": True, "msg": "已恢复"})


async def api_user_permanent_delete(request: web.Request) -> web.Response:
    """永久删除用户及其所有数据（不可恢复）。"""
    steam_id = request.match_info["steam_id"]
    from src.db.repository import permanent_delete_user
    ok = await permanent_delete_user(steam_id)
    if not ok:
        return web.json_response({"error": "用户不存在"}, status=404)
    return web.json_response({"ok": True, "msg": "已永久删除"})


# ---------------------------------------------------------------------------
# API: 库存数据
# ---------------------------------------------------------------------------

async def api_inventory(request: web.Request) -> web.Response:
    steam_id = request.match_info["steam_id"]
    snapshot = await load_current_snapshot(steam_id)
    if not snapshot:
        return web.json_response({"error": "no data"}, status=404)

    from collections import Counter
    from src.crawler.localize import translate_name

    name_count = Counter(i.market_hash_name for i in snapshot.items.values())
    items_summary = [
        {"name": n, "name_zh": translate_name(n), "count": c}
        for n, c in name_count.most_common()
    ]
    items_detail = []
    for item in snapshot.items.values():
        d = item.to_dict()
        d["name_zh"] = translate_name(item.market_hash_name)
        items_detail.append(d)

    return web.json_response({
        "steam_id": steam_id,
        "item_count": snapshot.item_count,
        "api_total_count": snapshot.api_total_count,
        "updated_at": _iso_utc(snapshot.captured_at),
        "summary": items_summary,
        "items": items_detail,
    })


async def api_changes(request: web.Request) -> web.Response:
    steam_id = request.match_info["steam_id"]
    limit = min(int(request.query.get("limit", "500")), 5000)  # 上限 5000
    changes = await get_recent_changes(steam_id, limit=limit)
    from src.crawler.localize import translate_name
    for c in changes:
        detail = c.get("detail") or {}
        # 从 detail 的各种嵌套结构中提取物品名并翻译
        name = (detail.get("market_hash_name")
                or detail.get("item", {}).get("market_hash_name")
                or detail.get("current_state", {}).get("market_hash_name")
                or "")
        if name:
            c["name_zh"] = translate_name(name)
    return web.json_response(changes)


async def api_archives(request: web.Request) -> web.Response:
    steam_id = request.match_info["steam_id"]
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(
            select(SnapshotArchive)
            .where(SnapshotArchive.steam_id == steam_id)
            .order_by(SnapshotArchive.captured_at.desc())
            .limit(30)
        )
        archives = result.scalars().all()

    items = []
    for a in archives:
        raw = zlib.decompress(a.snapshot_data)
        data = json.loads(raw)
        from collections import Counter
        name_count = Counter(i.get("market_hash_name", "?") for i in data.values())
        items.append({
            "id": a.id,
            "date": _fmt_archive_date(a.captured_at),
            "item_count": len(data),
            "top_items": [{"name": n, "count": c} for n, c in name_count.most_common(10)],
        })
    return web.json_response(items)


async def api_compare(request: web.Request) -> web.Response:
    """对比两个历史快照，返回差异。"""
    steam_id = request.match_info["steam_id"]
    id_a = int(request.query.get("a", "0"))
    id_b = int(request.query.get("b", "0"))
    if not id_a or not id_b:
        return web.json_response({"error": "需要参数 a 和 b（快照 ID）"}, status=400)

    from sqlalchemy import select
    async with async_session_factory() as session:
        result_a = await session.execute(select(SnapshotArchive).where(SnapshotArchive.id == id_a))
        result_b = await session.execute(select(SnapshotArchive).where(SnapshotArchive.id == id_b))
        snap_a = result_a.scalar_one_or_none()
        snap_b = result_b.scalar_one_or_none()

    if not snap_a or not snap_b:
        return web.json_response({"error": "快照不存在"}, status=404)

    from collections import Counter
    data_a = json.loads(zlib.decompress(snap_a.snapshot_data))
    data_b = json.loads(zlib.decompress(snap_b.snapshot_data))

    names_a = Counter(i.get("market_hash_name", "?") for i in data_a.values())
    names_b = Counter(i.get("market_hash_name", "?") for i in data_b.values())

    all_names = set(names_a.keys()) | set(names_b.keys())
    diff = []
    for name in sorted(all_names):
        ca = names_a.get(name, 0)
        cb = names_b.get(name, 0)
        if ca != cb:
            diff.append({"name": name, "count_a": ca, "count_b": cb, "delta": cb - ca})

    return web.json_response({
        "date_a": _fmt_archive_date(snap_a.captured_at),
        "date_b": _fmt_archive_date(snap_b.captured_at),
        "count_a": len(data_a),
        "count_b": len(data_b),
        "diff": sorted(diff, key=lambda x: abs(x["delta"]), reverse=True),
    })


# ---------------------------------------------------------------------------
# API: 监控控制
# ---------------------------------------------------------------------------

async def api_ws(request: web.Request) -> web.WebSocketResponse:
    """WebSocket 端点 — 实时推送库存变化。"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async with _state.ws_lock:
        _state.ws_clients.add(ws)
    logger.info("WebSocket 客户端已连接 (共 %d)", len(_state.ws_clients))
    try:
        async for msg in ws:
            pass  # 只用于服务端推送，不处理客户端消息
    finally:
        async with _state.ws_lock:
            _state.ws_clients.discard(ws)
        logger.info("WebSocket 客户端已断开 (共 %d)", len(_state.ws_clients))
    return ws


async def ws_broadcast(event_type: str, data: dict) -> None:
    """向所有 WebSocket 客户端广播消息（线程安全）。"""
    if not _state.ws_clients:
        return
    message = json.dumps({"type": event_type, "data": data}, default=str)
    async with _state.ws_lock:
        dead = set()
        for ws in _state.ws_clients:
            try:
                await ws.send_str(message)
            except Exception:
                dead.add(ws)
        _state.ws_clients -= dead


async def api_monitor_start(request: web.Request) -> web.Response:
    if _state.monitor_running:
        return web.json_response({"ok": True, "msg": "already running"})
    _state.monitor_task = asyncio.create_task(_monitor_loop())
    return web.json_response({"ok": True, "msg": "started"})


async def api_monitor_stop(request: web.Request) -> web.Response:
    if _state.monitor_task and not _state.monitor_task.done():
        _state.monitor_task.cancel()
        _state.monitor_running = False
        _state.heartbeat_event.set()  # 唤醒统一间隔模式的 worker
        # 唤醒分层调度模式的所有 tier worker
        for event in _state.tier_events.values():
            event.set()
        return web.json_response({"ok": True, "msg": "stopped"})
    return web.json_response({"ok": True, "msg": "not running"})


async def api_fetch_now(request: web.Request) -> web.Response:
    """立即执行一次爬取。"""
    from src.scheduler.monitor import monitor_all_users
    try:
        async def _on_user_done(result):
            await ws_broadcast("user_done", result)
        stats = await asyncio.wait_for(monitor_all_users(on_user_done=_on_user_done), timeout=300)
        return web.json_response({"ok": True, "stats": stats})
    except asyncio.TimeoutError:
        return web.json_response({"error": "timeout"}, status=504)
    except Exception:
        logger.exception("api_fetch_now 异常")
        return web.json_response({"error": "internal error"}, status=500)


async def api_create_snapshot(request: web.Request) -> web.Response:
    """手动为指定用户创建一次历史快照。"""
    steam_id = request.match_info["steam_id"]
    from src.db.repository import load_current_snapshot, save_daily_archive
    snapshot = await load_current_snapshot(steam_id)
    if not snapshot:
        return web.json_response({"error": "该用户暂无库存数据，请先执行一次爬取"}, status=404)
    await save_daily_archive(steam_id, snapshot.items, force=True)
    return web.json_response({
        "ok": True,
        "item_count": snapshot.item_count,
        "msg": f"快照已创建（{snapshot.item_count} 件物品）",
    })


async def api_schedule(request: web.Request) -> web.Response:
    """读取当前归档调度设置。"""
    return web.json_response({
        "snapshot_hour": settings.snapshot_hour,
        "snapshot_interval_hours": settings.snapshot_interval_hours,
        "fetch_interval_minutes": settings.fetch_interval_minutes,
    })


async def api_schedule_update(request: web.Request) -> web.Response:
    """更新归档调度设置并热更新调度器。"""
    data = await request.json()

    hour = data.get("snapshot_hour")
    interval = data.get("snapshot_interval_hours")
    fetch_min = data.get("fetch_interval_minutes")

    changed = False

    if hour is not None:
        hour = max(0, min(23, int(hour)))
        settings.snapshot_hour = hour
        changed = True

    if interval is not None:
        interval = max(0, int(interval))
        settings.snapshot_interval_hours = interval
        changed = True

    if fetch_min is not None:
        fetch_min = max(1, int(fetch_min))
        settings.fetch_interval_minutes = fetch_min
        changed = True

    if changed:
        # 热更新调度器
        _update_scheduler_jobs()

    return web.json_response({
        "ok": True,
        "snapshot_hour": settings.snapshot_hour,
        "snapshot_interval_hours": settings.snapshot_interval_hours,
        "fetch_interval_minutes": settings.fetch_interval_minutes,
    })


def _update_scheduler_jobs_internal(sched) -> None:
    """配置调度器任务（内部实现）。"""
    if settings.snapshot_interval_hours > 0:
        sched.add_job(
            _run_maintenance,
            "interval",
            hours=settings.snapshot_interval_hours,
            id="maintenance",
            name="快照归档",
            max_instances=1,
            replace_existing=True,
        )
    else:
        sched.add_job(
            _run_maintenance,
            "cron",
            hour=settings.snapshot_hour,
            minute=0,
            id="maintenance",
            name="每日快照归档",
            max_instances=1,
            replace_existing=True,
        )


def _update_scheduler_jobs() -> None:
    """热更新调度器中的任务配置。"""
    if _state.scheduler_ref is None:
        return
    # 移除旧的归档任务
    try:
        _state.scheduler_ref.remove_job("maintenance")
    except Exception:
        pass
    _update_scheduler_jobs_internal(_state.scheduler_ref)

    logger.info("调度器已更新: 归档间隔=%s, 归档时间=%s:00, 监控间隔=%s分钟",
                f"{settings.snapshot_interval_hours}h" if settings.snapshot_interval_hours else "每日",
                settings.snapshot_hour,
                settings.fetch_interval_minutes)


# ---------------------------------------------------------------------------
# 归档维护任务（被 APScheduler 调用）
# ---------------------------------------------------------------------------

async def _run_maintenance() -> None:
    """执行快照归档 + 清理过期数据。"""
    try:
        from src.scheduler.monitor import compact_maintenance
        await compact_maintenance()
        logger.info("定时归档完成")
    except Exception as e:
        logger.exception("定时归档异常: %s", e)


# ---------------------------------------------------------------------------
# 监控循环
# ---------------------------------------------------------------------------

async def _monitor_loop() -> None:
    """Web 触发的监控循环（支持分层调度与统一间隔双模式）。

    tiered_scheduling_enabled=True 时：
      启动 3 个独立的 per-tier 协程，各自按不同间隔运行。
    tiered_scheduling_enabled=False 时（默认）：
      沿用旧版的统一间隔循环（完全向后兼容）。
    """
    _state.monitor_running = True
    _state.last_heartbeat = 0.0
    # 重置所有 event 为待触发状态（用于快速响应 Stop）
    _state.heartbeat_event.clear()
    for event in _state.tier_events.values():
        event.clear()
    logger.info("Web 触发的监控循环已启动 (tiered=%s) — 7x24 持续运行，仅手动停止", settings.tiered_scheduling_enabled)

    try:
        if settings.tiered_scheduling_enabled:
            await _run_tiered_loop()
        else:
            await _run_unified_loop()
    except asyncio.CancelledError:
        pass
    finally:
        _state.monitor_running = False
        # 清理所有层级任务
        for tier, task in list(_state.tier_tasks.items()):
            if task and not task.done():
                task.cancel()
            _state.tier_tasks[tier] = None
        logger.info("监控循环已停止")


async def _run_unified_loop() -> None:
    """统一间隔模式（向后兼容）。持续运行直到手动 Stop，不再因心跳超时自动停止。"""
    from src.scheduler.monitor import monitor_all_users

    while _state.monitor_running:
        try:
            async def _on_user_done(result):
                await ws_broadcast("user_done", result)

            stats = await monitor_all_users(on_user_done=_on_user_done)
            _state.last_run_time = datetime.now(timezone.utc).isoformat()
            _state.last_run_stats = stats
            await ws_broadcast("update", {"time": _state.last_run_time, "stats": stats})
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("监控循环异常: %s", e)
            _state.last_run_stats = {"error": str(e)}

        # 等待下一轮（可通过 heartbeat_event 提前唤醒以响应 Stop）
        total_sleep = settings.fetch_interval_minutes * 60
        try:
            await asyncio.wait_for(_state.heartbeat_event.wait(), timeout=total_sleep)
        except asyncio.TimeoutError:
            pass
        _state.heartbeat_event.clear()


async def _run_tiered_loop() -> None:
    """分层调度模式：为 high/medium/low 各启动一个独立协程。"""
    from src.scheduler.monitor import monitor_tier, _tier_last_run, _tier_last_stats

    async def _tier_worker(tier: str, interval_minutes: int) -> None:
        """单个层级的监控 worker。持续运行直到手动 Stop，不再因心跳超时自动停止。"""
        tier_event = _state.tier_events[tier]  # 每个层级独立的 Event
        logger.info("层级 [%s] worker 已启动 (间隔 %d 分钟) — 7x24 运行", tier, interval_minutes)
        while _state.monitor_running:
            try:
                async def _on_user_done(result):
                    await ws_broadcast("user_done", result)

                stats = await monitor_tier(tier, on_user_done=_on_user_done)
                _tier_last_run[tier] = datetime.now(timezone.utc).isoformat()
                _tier_last_stats[tier] = stats
                await ws_broadcast("tier_update", {
                    "tier": tier,
                    "time": _tier_last_run[tier],
                    "stats": stats,
                })
                _state.last_run_time = _tier_last_run[tier]
                _state.last_run_stats = stats
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("层级 [%s] worker 异常: %s", tier, e)

            # 等待下一轮（可通过独立 event 提前唤醒以响应 Stop）
            if not _state.monitor_running:
                break
            try:
                await asyncio.wait_for(tier_event.wait(), timeout=interval_minutes * 60)
            except asyncio.TimeoutError:
                pass
            tier_event.clear()

        logger.info("层级 [%s] worker 已停止", tier)

    # 启动三个独立 worker
    tier_intervals = {
        "high": settings.tier_high_interval_minutes,
        "medium": settings.tier_medium_interval_minutes,
        "low": settings.tier_low_interval_minutes,
    }
    for tier, interval in tier_intervals.items():
        _state.tier_tasks[tier] = asyncio.create_task(_tier_worker(tier, interval))

    # 主循环：等待所有 worker 结束（不主动中断）
    try:
        tasks = [t for t in _state.tier_tasks.values() if t is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        _state.monitor_running = False
        # 唤醒所有层级 worker 的独立 event，并取消任务
        for tier, event in _state.tier_events.items():
            event.set()
        for tier, task in list(_state.tier_tasks.items()):
            if task and not task.done():
                task.cancel()
            _state.tier_tasks[tier] = None


# ---------------------------------------------------------------------------
# 分层调度：频率管理 API
# ---------------------------------------------------------------------------

async def api_frequency_status(request: web.Request) -> web.Response:
    """获取分层调度状态（各层级间隔、最后运行时间、每层用户数等）。"""
    from src.scheduler.monitor import get_tier_status
    status = get_tier_status()

    # 按层级统计用户数
    from src.db.repository import get_active_users_by_frequency
    grouped = await get_active_users_by_frequency()
    tier_user_counts = {tier: len(users) for tier, users in grouped.items()}

    return web.json_response({
        **status,
        "tier_user_counts": tier_user_counts,
    })


async def api_frequency_set_user(request: web.Request) -> web.Response:
    """设置指定用户的监控频率层级 (high/medium/low)。

    POST /api/users/{steam_id}/frequency
    Body: {"frequency": "high"}
    返回: {"ok": true, "steam_id": "...", "frequency": "high"}
    """
    steam_id = request.match_info["steam_id"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    frequency = data.get("frequency", "").strip().lower()
    if frequency not in ("high", "medium", "low"):
        return web.json_response(
            {"error": f"无效的频率层级: '{frequency}'，支持 high / medium / low"},
            status=400,
        )

    from src.db.repository import set_user_frequency
    ok = await set_user_frequency(steam_id, frequency)
    if not ok:
        return web.json_response({"error": "用户不存在"}, status=404)

    logger.info("用户 %s 频率已切换为 %s", steam_id, frequency)
    return web.json_response({"ok": True, "steam_id": steam_id, "frequency": frequency})


async def api_frequency_set_batch(request: web.Request) -> web.Response:
    """批量设置用户频率层级。

    POST /api/frequency/batch
    Body: {"users": {"7656119...": "high", "7656119...": "low"}}
    返回: {"ok": true, "updated": 2, "failed": 0}
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    users = data.get("users", {})
    if not isinstance(users, dict):
        return web.json_response({"error": "users 应为 {steam_id: frequency} 字典"}, status=400)

    from src.db.repository import set_user_frequency
    updated = 0
    failed = 0
    details: list[dict[str, Any]] = []

    for steam_id, freq in users.items():
        freq = str(freq).strip().lower()
        if freq not in ("high", "medium", "low"):
            failed += 1
            details.append({"steam_id": steam_id, "error": f"无效频率: {freq}"})
            continue
        ok = await set_user_frequency(steam_id, freq)
        if ok:
            updated += 1
            details.append({"steam_id": steam_id, "frequency": freq, "ok": True})
        else:
            failed += 1
            details.append({"steam_id": steam_id, "error": "用户不存在"})

    return web.json_response({
        "ok": True,
        "updated": updated,
        "failed": failed,
        "details": details,
    })


# ---------------------------------------------------------------------------
# 数据导出 / 导入 API
# ---------------------------------------------------------------------------

SAVES_DIR = (Path(__file__).parent.parent.parent / "saves").resolve()


async def api_export(request: web.Request) -> web.Response:
    """导出全部数据为 .cs2mon JSON 文件，保存到 ~/saves/ 目录。"""
    from src.db.repository import export_all_data
    try:
        data = await export_all_data()
    except Exception as e:
        logger.exception("导出数据失败")
        return web.json_response({"error": str(e)}, status=500)

    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"cs2mon_export_{ts}.cs2mon"
    filepath = SAVES_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info("数据已导出: %s (%d 用户)", filepath, data.get("user_count", 0))
    return web.json_response({
        "ok": True,
        "filename": filename,
        "filepath": str(filepath),
        "user_count": data.get("user_count", 0),
    })


async def api_export_download(request: web.Request) -> web.Response:
    """下载指定的导出文件。"""
    filename = request.query.get("file", "")
    if not filename:
        return web.json_response({"error": "missing file param"}, status=400)

    # 规范化路径并校验是否在允许目录内（防止路径穿越）
    filepath = (SAVES_DIR / filename).resolve()
    if not filepath.is_relative_to(SAVES_DIR.resolve()):
        return web.json_response({"error": "forbidden"}, status=403)

    if not filepath.exists() or not filepath.is_file():
        return web.json_response({"error": "文件不存在"}, status=404)

    if not filepath.name.endswith(".cs2mon"):
        return web.json_response({"error": "forbidden"}, status=403)

    return web.FileResponse(
        path=filepath,
        headers={
            "Content-Disposition": f'attachment; filename="{filepath.name}"',
            "Content-Type": "application/json; charset=utf-8",
        },
    )


async def api_import(request: web.Request) -> web.Response:
    """接收 .cs2mon 文件并导入数据。"""
    reader = await request.multipart()
    field = await reader.next()
    if field is None:
        return web.json_response({"error": "no file uploaded"}, status=400)

    raw = await field.read()
    try:
        text = raw.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return web.json_response({"error": f"文件格式错误: {e}"}, status=400)

    if data.get("app") != "steam-cs2-inventory-monitor":
        return web.json_response({"error": "不是有效的 CS2 监控器导出文件"}, status=400)

    from src.db.repository import import_all_data
    try:
        stats = await import_all_data(data)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        logger.exception("导入数据失败")
        return web.json_response({"error": str(e)}, status=500)

    logger.info("数据导入完成: 新建 %d, 更新 %d, 跳过 %d",
                stats["created"], stats["updated"], stats["skipped"])
    return web.json_response({"ok": True, "stats": stats})


# ---------------------------------------------------------------------------
# 连通性测试 API — 快速诊断 Steam API 是否可达
# ---------------------------------------------------------------------------

async def api_test_connection(request: web.Request) -> web.Response:
    """测试 Steam 库存 API 连通性。可指定 steam_id 否则用配置中的第一个。

    仅返回状态、耗时、成功与否，不暴露内部 URL 和代理配置。
    """
    import time as _time
    from src.crawler.fetcher import _build_headers, _get_proxy_config, get_client
    steam_id = request.query.get("steam_id", settings.steam_id_list[0] if settings.steam_id_list else "")
    if not steam_id:
        return web.json_response({"error": "no steam_id"}, status=400)
    proxy_cfg = _get_proxy_config()
    headers = _build_headers(proxy_cfg)
    url = settings.steam_inventory_url.format(steam_id=steam_id) + "?l=english&count=5"
    if proxy_cfg.get("hosts_override"):
        url = url.replace("https://steamcommunity.com", f"https://{proxy_cfg['hosts_override']}")
    try:
        client = await get_client()
        t0 = _time.monotonic()
        resp = await client.get(url, headers=headers, timeout=15)
        elapsed = round(_time.monotonic() - t0, 3)
        result: dict[str, Any] = {
            "ok": False, "status": resp.status_code, "elapsed": elapsed,
        }
        if resp.status_code == 200:
            try:
                data = resp.json()
                result["ok"] = bool(data and data.get("success"))
                result["assets"] = len(data.get("assets", [])) if data else 0
                result["total"] = data.get("total_inventory_count", 0) if data else 0
            except Exception:
                result["ok"] = False
                result["error"] = "JSON parse failed"
        elif resp.status_code == 429:
            result["error"] = "rate limited (429)"
        elif resp.status_code == 403:
            result["error"] = "forbidden (403) — inventory may be private"
        else:
            result["error"] = f"HTTP {resp.status_code}"
    except Exception as e:
        result = {"ok": False, "status": 0, "error": f"{type(e).__name__}: {e}"}
    return web.json_response(result)


# ---------------------------------------------------------------------------
# 应用初始化
# ---------------------------------------------------------------------------

def create_web_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])

    # 页面
    app.router.add_get("/", index)
    app.router.add_get("/index.html", index)

    # 静态文件
    if STATIC_DIR.exists():
        app.router.add_static("/static/", STATIC_DIR, show_index=False)

    # API 路由
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/login", api_login)
    app.router.add_post("/api/heartbeat", api_heartbeat)
    app.router.add_get("/api/users", api_users_list)
    app.router.add_post("/api/users", api_user_add)
    app.router.add_post("/api/users/batch", api_users_batch)
    app.router.add_put("/api/users/{steam_id}", api_user_update)
    app.router.add_delete("/api/users/{steam_id}", api_user_delete)
    app.router.add_get("/api/trash", api_trash_list)
    app.router.add_post("/api/users/{steam_id}/restore", api_user_restore)
    app.router.add_delete("/api/users/{steam_id}/permanent", api_user_permanent_delete)
    app.router.add_get("/api/users/{steam_id}/inventory", api_inventory)
    app.router.add_get("/api/users/{steam_id}/changes", api_changes)
    app.router.add_get("/api/users/{steam_id}/archives", api_archives)
    app.router.add_get("/api/users/{steam_id}/compare", api_compare)
    app.router.add_post("/api/monitor/start", api_monitor_start)
    app.router.add_post("/api/monitor/stop", api_monitor_stop)
    app.router.add_post("/api/monitor/fetch-now", api_fetch_now)
    app.router.add_post("/api/users/{steam_id}/snapshot", api_create_snapshot)
    app.router.add_get("/api/schedule", api_schedule)
    app.router.add_put("/api/schedule", api_schedule_update)
    app.router.add_get("/api/ws", api_ws)
    app.router.add_get("/ping", api_heartbeat)
    app.router.add_get("/health", api_status)
    # 分层调度 API
    app.router.add_get("/api/frequency/status", api_frequency_status)
    app.router.add_post("/api/users/{steam_id}/frequency", api_frequency_set_user)
    app.router.add_post("/api/frequency/batch", api_frequency_set_batch)
    # 数据导出 / 导入
    app.router.add_post("/api/export", api_export)
    app.router.add_get("/api/export/download", api_export_download)
    app.router.add_post("/api/import", api_import)
    # 连通性诊断
    app.router.add_get("/api/test-connection", api_test_connection)

    # 启动时初始化
    async def on_startup(app_: web.Application) -> None:
        await init_db()
        from src.db.repository import init_default_users
        await init_default_users()
        # 一次性初始化 Web 密码（避免运行时副作用）
        _init_web_password()
        logger.info("Web 服务启动，数据库已初始化")

    app.on_startup.append(on_startup)
    return app


async def start_web_server() -> None:
    """启动 Web 服务器 + APScheduler 归档调度（非阻塞）。"""
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()

    host = settings.health_server_host
    port = settings.health_server_port

    # 尝试绑定端口，冲突时自动递增
    bound = False
    for attempt in range(5):
        try:
            site = web.TCPSite(runner, host, port + attempt)
            await site.start()
            if attempt > 0:
                logger.warning("端口 %d 被占用，已切换到 %d", port, port + attempt)
                settings.health_server_port = port + attempt
            logger.info("Web 仪表盘已启动: http://%s:%d", host, port + attempt)
            bound = True
            break
        except OSError as e:
            if "10048" in str(e) or "address already in use" in str(e).lower():
                logger.warning("端口 %d 已被占用，尝试 %d...", port + attempt, port + attempt + 1)
                continue
            raise

    if not bound:
        logger.error("无法绑定端口（尝试了 %d-%d），请关闭占用端口的程序", port, port + 4)
        raise SystemExit(1)

    # 启动 APScheduler 归档调度
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    sched = AsyncIOScheduler()
    _state.scheduler_ref = sched

    _update_scheduler_jobs_internal(sched)

    sched.start()
    logger.info("归档调度已启动")
