"""SQLAlchemy ORM 模型 — 对应计划中的各表。"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base
from src.models.item import ChangeType


class MonitoredUser(Base):
    """被监控用户表。"""

    __tablename__ = "monitored_users"

    steam_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nickname: Mapped[str | None] = mapped_column(String(128), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    consecutive_fails: Mapped[int] = mapped_column(Integer, default=0)
    last_error_msg: Mapped[str | None] = mapped_column(Text, default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)  # 回收站
    monitor_frequency: Mapped[str] = mapped_column(String(16), default="medium")  # high | medium | low
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class CurrentInventoryState(Base):
    """当前库存基准快照 — 每个用户仅保留一条。"""

    __tablename__ = "current_inventory_state"

    steam_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_data: Mapped[bytes] = mapped_column(LargeBinary)  # zlib 压缩的 JSON
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    api_total_count: Mapped[int] = mapped_column(Integer, default=0)  # Steam API total_inventory_count


class InventoryChange(Base):
    """变化事件表。"""

    __tablename__ = "inventory_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    steam_id: Mapped[str] = mapped_column(String(64), index=True)
    change_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    change_type: Mapped[ChangeType] = mapped_column(Enum(ChangeType), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(64), default="")
    old_asset_id: Mapped[str | None] = mapped_column(String(64), default=None)
    detail: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)  # zlib 压缩的 JSON
    snapshot_before: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)


class SnapshotArchive(Base):
    """历史快照归档 — 每天一条。"""

    __tablename__ = "snapshot_archives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    steam_id: Mapped[str] = mapped_column(String(64), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    snapshot_data: Mapped[bytes] = mapped_column(LargeBinary)  # zlib 压缩的 JSON
