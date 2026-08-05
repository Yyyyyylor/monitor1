# Steam CS2 库存监控器 v2.2

监控 Steam CS2 库存变化，支持存储单元活动检测、历史归档、Web 仪表盘。

## 快速开始（Windows）

### 1. 解压后双击 `安装.bat`

自动完成：创建虚拟环境 → 安装依赖 → 初始化数据库

### 2. 编辑 `.env`

```
STEAM_IDS=你的Steam64位ID
STEAM_HOSTS_OVERRIDE=127.0.0.1:443
```

> Steam++ 需开启 **hosts 加速**模式，加速 Steam 社区

### 3. 双击 `启动.bat`

浏览器自动打开 `http://localhost:8080`

---

## 功能

### Web 仪表盘
- 🎯 物品分类浏览（步枪/手枪/匕首/手套等）
- 🖼️ 物品图片展示
- 🎨 磨损色条 + 品质徽章 + StatTrak 标识
- 🔍 按名称搜索、按磨损/名称排序
- 📋 物品详情弹窗（印花/磨损/图案种子/阶段）
- 🛒 一键跳转 Steam 市场

### 监控功能
- ⏱️ 定时爬取（默认 5 分钟）
- ⚡ 并发抓取（默认 3 路并发 + 每用户随机抖动，避免触发 Steam 限流）
- 📦 存储单元活动检测（基于 total_inventory_count delta）
- 🔄 交换识别（同类型物品配对）
- 📜 变化事件记录（added/removed/modified/swapped）
- 📅 每日归档快照（保留 90 天）
- 📢 双通道通知（用户通知 + 管理员告警）
- 🖥️ Web 启动后 7×24 持续监控，可随时手动停止

---

## 项目结构

```
├── 安装.bat              # 一键安装
├── 启动.bat              # 一键启动
├── run_web.py            # Web 仪表盘入口
├── src/
│   ├── main.py           # 纯监控入口（无 Web）
│   ├── config.py         # pydantic-settings 配置
│   ├── models/item.py    # 核心数据模型
│   ├── db/               # 数据库层
│   ├── crawler/          # 分页抓取 + 属性解析
│   ├── detector/         # 变更检测 + 交换识别 + 活动分类
│   ├── notifications/    # 通知服务
│   ├── scheduler/        # 监控调度
│   ├── health/           # 健康检查
│   └── web/              # Web 仪表盘
│       ├── app.py        # API 路由
│       └── static/
│           └── index.html
├── tests/                # 测试（59 个用例）
├── requirements.lock     # 依赖锁定（可复现安装）
├── CHANGELOG.md          # 更新日志
├── LICENSE               # MIT 许可证
├── .env.example          # 配置模板
├── Dockerfile
└── docker-compose.yml
```

## 手动运行

```bash
pip install -e .
python run_web.py
```

## Docker

```bash
docker compose up -d
```

## 测试

```bash
pip install -e ".[dev]"
pytest
```

## 依赖锁定

`requirements.lock` 记录了当前工作环境验证过的精确依赖版本（含传递依赖），用于可复现安装与依赖漏洞审计：

```bash
# 按锁定版本复现安装
pip install -r requirements.lock

# 依赖漏洞扫描
pip install pip-audit
pip-audit -r requirements.lock
```

> 新环境首次部署仍建议 `pip install -e .`；需要更新锁定时执行 `venv/Scripts/python -m pip freeze --exclude-editable > requirements.lock`。

---
### 鸣谢

感谢 @八月 对本项目提供的所有支持

## License

本项目采用 [MIT License](LICENSE) 开源许可。

Copyright (c) 2026 Cheney

基于本项目进行使用、修改、分发或再分发时，须保留上述版权声明与许可条款（详见 [LICENSE](LICENSE) 文件全文）。
