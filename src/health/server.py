"""健康检查 HTTP 服务 — 用于 UptimeRobot 等外部监控。"""

from __future__ import annotations

import json
import logging

from aiohttp import web

from src.config import settings

logger = logging.getLogger(__name__)


async def _health_check(_request: web.Request) -> web.Response:
    """健康检查端点。返回 200 及运行状态信息。"""
    # 优先统计数据库中的活跃用户（含 Web 界面添加的用户），
    # 数据库不可用时回退到配置里的 steam_id_list，避免数字误导。
    monitored_users = len(settings.steam_id_list)
    try:
        from src.db.repository import get_active_users
        monitored_users = len(await get_active_users())
    except Exception:
        pass
    status = {
        "status": "ok",
        "last_success_time": settings.last_success_time or "never",
        "last_fail_count": settings.last_fail_count,
        "monitored_users": monitored_users,
    }
    return web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(status, ensure_ascii=False),
    )


async def _ping(_request: web.Request) -> web.Response:
    """轻量存活检查。"""
    return web.Response(status=200, text="pong")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", _health_check)
    app.router.add_get("/ping", _ping)
    return app


async def start_health_server() -> None:
    """启动健康检查 HTTP 服务器（非阻塞）。端口冲突时自动递增，避免静默崩溃。"""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()

    host = settings.health_server_host
    port = settings.health_server_port
    bound = False
    for attempt in range(5):
        try:
            site = web.TCPSite(runner, host, port + attempt)
            await site.start()
            if attempt > 0:
                logger.warning("端口 %d 被占用，已切换到 %d", port, port + attempt)
                settings.health_server_port = port + attempt
            logger.info("健康检查服务已启动: http://%s:%d", host, port + attempt)
            bound = True
            break
        except OSError as e:
            if "address already in use" in str(e).lower():
                logger.warning("端口 %d 已被占用，尝试 %d...", port + attempt, port + attempt + 1)
                continue
            raise

    if not bound:
        logger.error("无法绑定端口（尝试了 %d-%d），请关闭占用端口的程序", port, port + 4)
        raise SystemExit(1)
