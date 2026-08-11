"""将 .env 中的 WEB_PASSWORD 原子迁移为 scrypt 校验值。"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.web.app import hash_password_for_storage


def _value_after_equals(line: str) -> str:
    value = line.split("=", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _write_password_hash(env_path: Path, password: str, *, require_empty_hash: bool) -> None:
    if not password:
        raise ValueError("Password must not be empty")
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if require_empty_hash and any(
        line.startswith("WEB_PASSWORD_HASH=") and _value_after_equals(line) for line in lines
    ):
        raise ValueError("WEB_PASSWORD_HASH is already configured; no migration was performed")

    encoded = hash_password_for_storage(password)
    output: list[str] = []
    hash_written = False
    for line in lines:
        if line.startswith("WEB_PASSWORD_HASH="):
            output.append(f"WEB_PASSWORD_HASH={encoded}\n")
            hash_written = True
        elif line.startswith("WEB_PASSWORD="):
            output.append("WEB_PASSWORD=\n")
        else:
            output.append(line)
    if not hash_written:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += "\n"
        output.append(f"WEB_PASSWORD_HASH={encoded}\n")

    fd, temp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=env_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.writelines(output)
        os.replace(temp_name, env_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def migrate(env_path: Path) -> None:
    """将未迁移的 WEB_PASSWORD 转换为 WEB_PASSWORD_HASH。"""
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    password = next(
        (_value_after_equals(line) for line in lines if line.startswith("WEB_PASSWORD=")),
        "",
    )
    if not password:
        raise ValueError("WEB_PASSWORD is missing or empty; no migration was performed")
    _write_password_hash(env_path, password, require_empty_hash=True)


def set_password(env_path: Path, password: str) -> None:
    """以新的 scrypt 校验值替换现有 Web 密码，不保留明文。"""
    _write_password_hash(env_path, password, require_empty_hash=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--password-env",
        help="从指定环境变量读取新密码；不会将密码写入命令行输出。",
    )
    args = parser.parse_args()
    if args.password_env:
        set_password(args.env_file, os.environ.get(args.password_env, ""))
        print("WEB_PASSWORD_HASH was updated.")
    else:
        migrate(args.env_file)
        print("WEB_PASSWORD was migrated to WEB_PASSWORD_HASH.")
