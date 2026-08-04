"""数据库迁移辅助函数测试（回归 L7）。"""

from __future__ import annotations

from src.db.database import _is_duplicate_column_error


class TestIsDuplicateColumnError:
    def test_duplicate_column_detected(self) -> None:
        exc = Exception("duplicate column name: monitor_frequency")
        assert _is_duplicate_column_error(exc) is True

    def test_other_errors_not_duplicate(self) -> None:
        exc = Exception("no such table: monitored_users")
        assert _is_duplicate_column_error(exc) is False
