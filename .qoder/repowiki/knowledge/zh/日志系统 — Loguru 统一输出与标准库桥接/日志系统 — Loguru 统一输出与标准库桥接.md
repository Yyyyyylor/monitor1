---
kind: logging_system
name: 日志系统 — Loguru 统一输出与标准库桥接
category: logging_system
scope:
    - '**'
source_files:
    - src/utils.py
    - src/main.py
    - run_web.py
    - src/config.py
---

本项目采用 **Loguru** 作为统一的日志框架，并通过自定义拦截器将 Python 标准库 `logging` 模块的输出桥接到 Loguru，实现全项目一致的日志格式、级别控制与文件轮转。

### 1. 使用的框架与工具
- **核心框架**: `loguru`（高性能结构化日志）
- **桥接机制**: 自定义 `logging.Handler`（`_Intercept`）将标准库 `logging` 记录转发到 Loguru
- **配置来源**: `src/config.py` 中的 `Settings.log_level`（默认 `INFO`），从 `.env` 加载

### 2. 核心文件与位置
- `src/utils.py` — `setup_logging()` 函数集中定义日志初始化逻辑，包括控制台输出、文件轮转、标准库桥接
- `src/main.py` 与 `run_web.py` — 两个入口均在启动时调用 `setup_logging(settings.log_level)` 完成全局日志配置
- `data/logs/` — 按日期分文件的日志存储目录，文件名格式 `monitor_YYYY-MM-DD.log`

### 3. 架构与设计决策
- **统一入口**: 所有模块通过 `from loguru import logger` 使用同一个全局 logger 实例，避免多实例冲突
- **双输出通道**:
  - 控制台: 彩色输出，便于开发调试
  - 文件: 每日自动轮转（`rotation="1 day"`），保留 30 天并 gzip 压缩
- **标准库兼容**: 通过 `_Intercept(logging.Handler)` 捕获第三方库或标准库的 `logging` 调用，统一走 Loguru 管道
- **格式化模板**: `{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {name}:{function}:{line} | {message}`，包含时间、级别、模块名、函数名、行号与消息

### 4. 约定与约束
- **日志级别**: 由 `settings.log_level` 控制，支持标准级别（DEBUG/INFO/WARNING/ERROR/CRITICAL），默认 INFO
- **文件命名**: 日志文件严格按 `monitor_{time:YYYY-MM-DD}.log` 命名，便于按日归档
- **生命周期管理**: 应用退出时需调用 `close_db()` 和 `close_client()`，但日志本身无需显式关闭（Loguru 自动处理）
- **异常处理**: 通过 `record.exc_info` 传递异常信息，确保堆栈跟踪完整输出
- **跨平台信号**: Windows 下使用 Ctrl+C 中断，非 Windows 平台注册 SIGINT/SIGTERM 信号处理器