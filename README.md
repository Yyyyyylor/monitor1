# Steam CS2 库存监控器 v2.2

> **简体中文** · [English](README_EN.md)

监控 Steam CS2 库存变化，支持存储单元活动检测、历史归档、Web 仪表盘。

## 快速开始（Windows）

### 1. 解压后双击 `install.bat`

自动完成：创建虚拟环境 → 安装依赖 → 初始化数据库

### 2. 编辑 `.env`

```
STEAM_IDS=你的Steam64位ID
STEAM_HOSTS_OVERRIDE=127.0.0.1:443
```

Web 管理端默认要求 HTTPS。仅在本机直接运行时，可额外设置
`WEB_ALLOW_INSECURE_HTTP=true`；Docker 默认只绑定 `127.0.0.1`，公网部署必须
通过可信的 HTTPS 反向代理，并设置 `WEB_TRUST_PROXY_HEADERS=true`。

不要把明文 `WEB_PASSWORD` 提交、备份或放入镜像。请使用 `.env.example` 中的
`WEB_PASSWORD_HASH` 格式保存 scrypt 校验值。

## 安全与部署（v2.3.0）

- 管理密码仅以 `WEB_PASSWORD_HASH` 的 scrypt 校验值保存；可使用
  `scripts/migrate_web_password.py --password-env <环境变量名>` 更新密码，脚本不会写回明文。
- 会话仅保存在短期、可撤销的 `HttpOnly` Cookie 中；退出登录会立即使当前会话失效。
- 数据导入在写入前会验证 Steam ID、文件大小、嵌套深度和库存/历史记录上限；失败时整批回滚。
- Docker 默认仅发布到 `127.0.0.1:8080`，以非 root 和只读根文件系统运行。公网访问必须通过 HTTPS 反向代理。

> Steam++ 需开启 **hosts 加速**模式，加速 Steam 社区

### 3. 双击 `start.bat`

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
├── install.bat           # 一键安装
├── start.bat             # 一键启动
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
├── tests/                # 测试（71 个用例）
├── requirements.lock     # 依赖锁定（可复现安装）
├── CHANGELOG.md          # 更新日志
├── README_EN.md          # 英文文档
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
