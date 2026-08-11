"""Steam CS2 库存监控器 — Web 仪表盘入口。

启动后自动初始化数据库、启动 Web 服务，打开浏览器访问 http://localhost:8080
"""

from __future__ import annotations

import asyncio
import signal
import sys

from src.config import settings
from src.utils import setup_logging

logger = None


async def main() -> None:
    global logger
    setup_logging(settings.log_level)
    from loguru import logger as _logger
    logger = _logger

    print("=" * 50)
    logger.info("Steam CS2 库存监控器 v2.4.0 — Web 仪表盘")
    print("=" * 50)

    # 启动 Web 服务（内含数据库初始化 + APScheduler）
    from src.web.app import start_web_server
    await start_web_server()

    port = settings.health_server_port
    logger.info("请在浏览器打开: http://localhost:{port}", port=port)

    # 打开浏览器
    import webbrowser
    webbrowser.open(f"http://localhost:{port}")

    # 等待终止信号
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

    from src.db.database import close_db
    from src.crawler.fetcher import close_client
    await close_db()
    await close_client()
    logger.info("已安全关闭")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
