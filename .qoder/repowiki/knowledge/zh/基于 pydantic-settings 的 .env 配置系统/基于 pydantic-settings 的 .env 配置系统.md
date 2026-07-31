---
kind: configuration_system
name: 基于 pydantic-settings 的 .env 配置系统
category: configuration_system
scope:
    - '**'
source_files:
    - src/config.py
    - .env.example
    - src/main.py
    - run_web.py
    - pyproject.toml
---

该项目使用 **pydantic-settings** 作为统一的配置加载与验证框架，所有运行时配置通过 `.env` 文件（或环境变量）注入，由 `src/config.py` 中的 `Settings` 类集中管理。

### 核心架构
- **单一配置源**：`src/config.py` 定义 `Settings(BaseSettings)` 类，通过 `model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")` 指定从项目根目录的 `.env` 文件加载配置，忽略未声明的额外字段。
- **全局单例**：模块末尾导出 `settings = Settings()`，各模块直接 `from src.config import settings` 获取配置实例，避免重复解析。
- **类型化默认值**：每个配置项都有明确的 Python 类型和默认值（如 `database_url: str = "sqlite+aiosqlite:///./data/monitor.db"`、`fetch_interval_minutes: int = 60`），提供开箱即用的安全默认行为。

### 配置分层与组织
配置按功能域分组，遵循清晰的命名约定：
- **数据库**：`DATABASE_URL`
- **监控目标**：`STEAM_IDS`（逗号分隔字符串，通过 `steam_id_list` 属性自动解析为列表）
- **爬取参数**：`FETCH_INTERVAL_MINUTES`、`REQUEST_DELAY_SECONDS`、`PAGE_SIZE`、`REQUEST_TIMEOUT_SECONDS`、`MAX_RETRIES`
- **代理设置**：`STEAM_PROXY_MODE`（auto/manual/hosts/none）、`STEAM_PROXY_URL`、`STEAM_HOSTS_OVERRIDE`、`STEAM_COOKIE`
- **通知系统**：用户通知（`USER_NOTIFY_ENABLED`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`DINGTALK_WEBHOOK_URL`、`SERVERCHAN_KEY`）与管理员告警（`ADMIN_NOTIFY_ENABLED`、`ADMIN_WEBHOOK_URL`、`ADMIN_WEBHOOK_TYPE`、`CONSECUTIVE_FAIL_THRESHOLD`）
- **Web 服务**：`HEALTH_SERVER_HOST`、`HEALTH_SERVER_PORT`、`WEB_PASSWORD`
- **存储维护**：`CHANGE_RETENTION_DAYS`、`ARCHIVE_RETENTION_DAYS`、`COMPACT_HOUR`
- **日志**：`LOG_LEVEL`
- **Steam API**：`STEAM_INVENTORY_URL`、`STEAM_USER_AGENT`
- **运行时状态**：`LAST_SUCCESS_TIME`、`LAST_FAIL_COUNT`（代码更新，非配置文件）

### 启动流程中的配置使用
- CLI 入口 `src/main.py` 在启动时调用 `setup_logging(settings.log_level)` 初始化日志，并通过 `settings.fetch_interval_minutes`、`settings.compact_hour` 等配置 APScheduler 任务。
- Web 入口 `run_web.py` 同样先加载配置再启动 Web 服务，支持自动打开浏览器访问仪表盘。
- 健康检查服务器 `src/health/server.py` 使用 `settings.health_server_host` 和 `settings.health_server_port` 绑定监听地址。

### 约定与约束
- 配置文件必须命名为 `.env` 并位于项目根目录（由 `env_file=".env"` 强制）
- 新增配置项需在 `Settings` 类中声明对应字段，否则会被 `extra="ignore"` 策略静默丢弃
- 复杂类型（如 Steam ID 列表）通过 Python 属性方法而非 pydantic 验证器处理，保持简单性
- 敏感信息（Cookie、Token、密钥）以明文存储在 `.env` 中，需配合 `.gitignore` 保护
- 所有布尔开关默认关闭（`False`），需要显式启用才生效的安全设计