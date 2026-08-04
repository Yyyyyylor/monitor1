"""Steam CS2 库存监控器 — CLI 入口（无 Web 界面）。

启动流程:
  1. 初始化数据库
  2. 配置 APScheduler 定时任务
  3. 阻塞运行

推荐使用 run_web.py 启动（带 Web 仪表盘）。
"""

from __future__ import annotations

import asyncio
import signal
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import settings
from src.utils import setup_logging


async def _init_application() -> None:
    from src.db.database import init_db
    from src.db.repository import init_default_users
    await init_db()
    await init_default_users()


async def _run_maintenance() -> None:
    from src.scheduler.monitor import compact_maintenance
    await compact_maintenance()


async def _run_monitor() -> None:
    from src.scheduler.monitor import monitor_all_users
    await monitor_all_users()


async def main() -> None:
    setup_logging(settings.log_level)
    from loguru import logger

    print("=" * 50)
    logger.info("Steam CS2 库存监控器 v2.2 启动")
    print("=" * 50)

    await _init_application()

    from src.health.server import start_health_server
    await start_health_server()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_monitor, "interval",
        minutes=settings.fetch_interval_minutes,
        id="monitor", max_instances=1, replace_existing=True, misfire_grace_time=60,
    )
    scheduler.add_job(
        _run_maintenance, "cron",
        hour=settings.compact_hour, minute=0,
        id="maintenance", max_instances=1, replace_existing=True,
    )
    scheduler.start()
    logger.info("调度器已启动: 监控间隔 {} 分钟, 维护时间 {}:00",
                settings.fetch_interval_minutes, settings.compact_hour)

    stop_event = asyncio.Event()
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: stop_event.set())
    else:
        logger.info("按 Ctrl+C 可安全关闭程序")

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass

    scheduler.shutdown(wait=False)
    from src.db.database import close_db
    from src.crawler.fetcher import close_client
    await close_db()
    await close_client()
    logger.info("监控器已安全关闭")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
