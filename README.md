# Steam CS2 库存监控器 v2.0

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
- 📦 存储单元活动检测（基于 total_inventory_count delta）
- 🔄 交换识别（同类型物品配对）
- 📜 变化事件记录（added/removed/modified/swapped）
- 📅 每日归档快照（保留 90 天）
- 📢 双通道通知（用户通知 + 管理员告警）
- 🖥️ 关闭网页自动停止监控（心跳机制）

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
├── tests/                # 测试（34 个用例）
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
