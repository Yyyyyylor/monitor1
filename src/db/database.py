"""数据库引擎与会话管理。"""

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

_is_sqlite = "sqlite" in settings.database_url

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_size=1,
    max_overflow=0,
)

# SQLite 默认不启用外键约束，需显式开启
if _is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession]:
    """FastAPI 风格的依赖注入会话。"""
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    """创建所有表，并自动执行必要的 schema 迁移。"""
    async with engine.begin() as conn:
        from src.db.models import Base as AllModels  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)

    # ---- Schema 迁移（独立连接，不污染 create_all 的事务） ----
    migrations = [
        "ALTER TABLE monitored_users ADD COLUMN monitor_frequency VARCHAR(16) DEFAULT 'medium'",
    ]
    for sql_text in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql_text))
        except Exception:
            pass  # 列已存在则忽略


async def close_db() -> None:
    """关闭数据库引擎。"""
    await engine.dispose()
