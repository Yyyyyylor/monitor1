"""公共工具函数。"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path


def iso_utc(dt: datetime | date | None) -> str | None:
    """将 datetime/date 转为 ISO 字符串，datetime 带 Z 后缀。"""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        s = dt.isoformat()
        return s + "Z" if not dt.tzinfo else s
    return dt.isoformat()  # date 对象直接返回日期部分


def setup_logging(log_level: str = "INFO") -> None:
    """统一日志初始化（loguru + 标准 logging 桥接）。"""
    import loguru

    fmt = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {name}:{function}:{line} | {message}"

    loguru.logger.remove()
    loguru.logger.add(sys.stdout, format=fmt, level=log_level.upper(), colorize=True)

    logs_dir = Path("data") / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    loguru.logger.add(
        logs_dir / "monitor_{time:YYYY-MM-DD}.log",
        format=fmt, level=log_level.upper(), rotation="1 day", retention="30 days", compression="gz",
    )

    class _Intercept(logging.Handler):
        def emit(self, record):
            loguru.logger.opt(depth=6, exception=record.exc_info).log(record.levelname, record.getMessage())

    logging.basicConfig(handlers=[_Intercept()], level=logging.WARNING, force=True)
