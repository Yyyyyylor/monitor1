"""应用配置 — 基于 pydantic-settings 从 .env 加载。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 数据库
    database_url: str = "sqlite+aiosqlite:///./data/monitor.db"

    # 监控目标（逗号分隔字符串，读取后自动分裂）
    # 注意：类型为 str 而非 list，避免 pydantic-settings 尝试 JSON 解码
    steam_ids: str = ""

    @property
    def steam_id_list(self) -> list[str]:
        """返回解析后的 Steam ID 列表。"""
        if not self.steam_ids or not self.steam_ids.strip():
            return []
        return [s.strip() for s in self.steam_ids.split(",") if s.strip()]

    # 爬取设置
    fetch_interval_minutes: int = 60
    request_delay_seconds: float = 1.2
    page_size: int = 2000
    request_timeout_seconds: int = 30
    max_retries: int = 3
    # 并发抓取用户数（Steam 对同 IP 并发敏感，默认保守取 3，建议结合实测调整）
    fetch_concurrency: int = 3
    # 每用户起始随机抖动上限（秒），打散请求避免对齐 Steam 限流窗口
    fetch_jitter_seconds: float = 1.5

    # ---- 分层调度（Tiered Scheduling） ----
    # 设 tiered_scheduling_enabled=false 则完全沿用旧版的 fetch_interval_minutes 统一间隔
    tiered_scheduling_enabled: bool = False
    # 各层级间隔（分钟），可通过 Web 界面动态修改
    tier_high_interval_minutes: int = 5     # 高频：5 分钟
    tier_medium_interval_minutes: int = 10   # 中频：10 分钟
    tier_low_interval_minutes: int = 20      # 低频：20 分钟
    # 层级间请求间隔（秒），减少 Steam 限流风险
    tier_user_spacing_seconds: float = 1.5

    # 用户通知
    user_notify_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    dingtalk_webhook_url: str = ""
    serverchan_key: str = ""

    # 管理员通知
    admin_notify_enabled: bool = False
    admin_webhook_url: str = ""
    admin_webhook_type: str = "telegram"
    consecutive_fail_threshold: int = 3

    # 健康检查
    # 默认仅监听本机回环，避免把 Web 看板与健康接口暴露到局域网/公网。
    # 需从其他设备访问时改为 0.0.0.0（Docker 容器内必须为 0.0.0.0 才能端口映射），
    # 并建议前置反向代理提供 HTTPS。
    health_server_host: str = "127.0.0.1"
    health_server_port: int = 8080

    # Web 访问密码
    # 留空则首次启动时自动生成随机密码并打印到控制台
    web_password: str = ""

    # 存储维护
    change_retention_days: int = 7
    archive_retention_days: int = 90
    compact_hour: int = 3

    # 日志
    log_level: str = "INFO"

    # Steam API 端点
    steam_inventory_url: str = "https://steamcommunity.com/inventory/{steam_id}/730/2"
    steam_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    # 代理设置（Steam++ 加速模式时使用，空字符串 = 不使用代理）
    # 代理设置
    # proxy_mode: auto=自动检测 | manual=使用下方URL | hosts=hosts直连 | none=不使用代理
    steam_proxy_mode: str = "auto"
    # manual 模式时填写（如 http://127.0.0.1:27015 或 socks5://127.0.0.1:1080）
    steam_proxy_url: str = ""
    # hosts 模式时填写（如 127.0.0.1:443）
    steam_hosts_override: str = ""
    # Steam 登录 Cookie（可选）— 用于访问私密库存或绕过部分限流
    # 从浏览器 F12 → Application → Cookies → steamcommunity.com → steamLoginSecure 复制
    steam_cookie: str = ""

    # 运行时状态（不来自 .env，通过代码更新）
    last_success_time: str = ""
    last_fail_count: int = 0

    # 运行时可调：快照归档调度（可通过 Web 界面修改）
    snapshot_hour: int = 3          # 每日归档执行小时（0-23）
    snapshot_interval_hours: int = 0  # 0=仅每日一次，>0=每 N 小时归档一次


settings = Settings()
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
