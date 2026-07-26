"""健康检查 HTTP 服务 — 用于 UptimeRobot 等外部监控。"""

from __future__ import annotations

import json
import logging

from aiohttp import web

from src.config import settings

logger = logging.getLogger(__name__)


async def _health_check(_request: web.Request) -> web.Response:
    """健康检查端点。返回 200 及运行状态信息。"""
    status = {
        "status": "ok",
        "last_success_time": settings.last_success_time or "never",
        "last_fail_count": settings.last_fail_count,
        "monitored_users": len(settings.steam_id_list),
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
    """启动健康检查 HTTP 服务器（非阻塞）。"""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.health_server_host, settings.health_server_port)
    await site.start()
    logger.info(
        "健康检查服务已启动: http://%s:%d",
        settings.health_server_host, settings.health_server_port,
    )
