---
kind: dependency_management
name: Python 依赖管理：基于 pyproject.toml 与 venv 的声明式依赖体系
category: dependency_management
scope:
    - '**'
source_files:
    - pyproject.toml
    - install.bat
    - Dockerfile
    - docker-compose.yml
    - src/steam_cs2_inventory_monitor.egg-info/requires.txt
---

本项目采用现代 Python 项目标准，通过 `pyproject.toml` 集中声明运行时与开发依赖，配合虚拟环境（venv）和 pip 进行安装与版本锁定。具体实践如下：

1. **依赖声明与版本约束**
   - 所有第三方库在 `pyproject.toml` 的 `[project].dependencies` 中统一声明，使用 `>=x.y,<z` 的半开区间约束，既允许小版本升级又防止破坏性大版本更新。
   - 运行期依赖包括 httpx、APScheduler、SQLAlchemy(asyncio)、aiosqlite、pydantic-settings、pydantic、loguru、aiohttp。
   - 开发依赖通过 `[project.optional-dependencies]dev` 分组，包含 pytest、pytest-asyncio、pytest-mock 等测试工具。
   - `requires-python = ">=3.10"` 明确最低 Python 版本要求。

2. **构建系统与包分发**
   - 使用 setuptools 作为构建后端（`setuptools.build_meta`），包名 `steam-cs2-inventory-monitor`，版本 `2.0.0`。
   - 源码位于 `src/` 目录，通过 `[tool.setuptools.packages.find] where = ["src"]` 自动发现。
   - 生成的 `src/steam_cs2_inventory_monitor.egg-info/requires.txt` 与 `pyproject.toml` 保持同步，供旧版 pip 兼容。

3. **虚拟环境与安装流程**
   - Windows 用户通过 `install.bat` 一键安装：检测/安装 Python 3.12 → 创建 `venv/` → 优先从官方 PyPI 安装 → 失败时回退到清华镜像源 `https://pypi.tuna.tsinghua.edu.cn/simple`。
   - 使用 `pip install -e .` 以可编辑模式安装，便于开发调试。
   - 首次运行前自动初始化数据库并生成 `.env` 配置文件。

4. **容器化部署中的依赖管理**
   - `Dockerfile` 采用分层缓存优化：先复制 `pyproject.toml` 再执行 `pip install --no-cache-dir .`，利用 Docker 层缓存加速重复构建。
   - 基础镜像为 `python:3.12-slim`，生产环境不安装构建工具，减小镜像体积。
   - `docker-compose.yml` 将 `.env` 以只读卷方式注入容器，数据目录持久化到宿主机 `./data`。

5. **测试依赖隔离**
   - 测试套件通过 `pytest` 运行，配置在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 中启用 `asyncio_mode = "auto"`。
   - 测试路径限定为 `tests/` 目录，避免误测生产代码。

6. **未使用的策略**
   - 项目中不存在 `requirements.txt`、`poetry.lock`、`Pipfile.lock` 等锁文件，依赖版本由 `pyproject.toml` 区间约束控制，无严格锁定机制。
   - 未使用私有 PyPI 仓库或 vendoring 策略，仅在内网不通时回退到清华镜像。