# 更新日志（Changelog）

本项目所有重要变更均记录在此文件中。

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本控制（SemVer）](https://semver.org/lang/zh-CN/)。

## [Unreleased]

（暂无）

## [2.2.0] - 2026-08-04

### Added

- 并发抓取：`_do_monitor_users` 改为有界并发（`asyncio.Semaphore`），多用户不再串行等待，整轮耗时从「用户数 × 单用户耗时」降为「ceil(用户数 / 并发) × 单用户耗时」（网络请求为瓶颈时收益明显）
- 新增配置 `FETCH_CONCURRENCY`（默认 3，范围 1~8）与 `FETCH_JITTER_SECONDS`（默认 1.5，每用户起始随机抖动上限）

### Changed

- 监控循环移除固定的用户间串行间隔，改为「有界并发 + 随机抖动」调度，打散请求避免对齐 Steam 限流窗口
- 单用户处理逻辑抽取为 `_process_one_user`，成功 / 失败统计与连续失败告警行为保持不变
- README 明确 Web 监控为 7×24 持续运行、可手动停止（此前误写为「关闭网页自动停止监控」，与代码行为不符）

### Performance

- 多用户抓取并行化，显著降低整轮监控耗时（受 `FETCH_CONCURRENCY` 限制，避免过度触发 Steam 限流）

## [2.1.0] - 2026-08-04

本次为「安全加固 + 性能优化 + 可靠性」版本，覆盖代码审计发现的高危 / 中危 / 低危项修复。

### Added

- 登录限速持久化：失败次数与锁定截止时间写入数据库（`login_rate_limit` 表），重启服务后限速状态不丢失
- 数据库级联删除：删除监控用户时自动清理其当前库存、变化事件与归档快照（外键 `ON DELETE CASCADE`）
- 分层调度响应优化：高频 / 中频 / 低频每个层级使用独立唤醒事件，手动停止监控时各层 worker 立即退出
- 数据导出分批处理：按用户分批查询与逐条解压，每处理 10 个用户让出一次事件循环，避免长时间阻塞与内存峰值
- 依赖锁定：新增 `requirements.lock`，固定验证过的精确依赖版本，支持 `pip install -r` 复现安装与 `pip-audit` 漏洞扫描

### Changed

- 认证加固：登录 token 改为对密码做 SHA-256 哈希后再参与 HMAC 签名，避免明文密码进入签名数据
- 密码与 token 校验改为常量时间比较（`hmac.compare_digest`），缓解时序侧信道攻击
- Web 密码改为启动时一次性初始化（`_init_web_password`）
- 通知模块（用户通知 + 管理员告警）复用全局共享的 httpx 连接池客户端
- 通知发送增加指数退避重试（`post_json_with_retry`，默认重试 2 次），发送失败不再直接丢弃
- 快照读取缓存升级为 LRU 策略（`OrderedDict`，上限 128 条）
- 分页抓取策略调整：任一页失败即整体失败（返回 `None`），不再返回不完整库存，避免与上一轮快照 diff 出大量虚假 `REMOVED` 事件并触发告警
- 变化事件接口 `limit` 参数设置硬上限并钳制到 `[1, 5000]`
- TLS 证书校验策略：仅本地回环代理（`127.0.0.1` / `localhost` / `::1`）允许关闭校验，远程代理必须校验，防止 `steamLoginSecure` cookie 与私密库存被中间人截获；同时移除对 `InsecureRequestWarning` 的全局压制
- Web 服务默认监听 `127.0.0.1`（原 `0.0.0.0`），避免暴露到局域网 / 公网；Docker 容器内显式指定 `0.0.0.0` 以支持端口映射
- 健康检查端口冲突处理：绑定失败时自动递增尝试并给出清晰错误，不再静默崩溃
- 分层调度状态写入收敛为公开 `record_tier_run()` 接口，Web 层不再直接改写调度器私有状态
- 快照序列化按 `asset_id` 排序，保证相同库存内容产出字节一致
- Docker 镜像补充复制 `translate/translation_map.json`，修复汉化数据缺失

### Fixed

- 修复 SQLite 默认未启用外键约束导致级联关系失效的问题（连接时显式执行 `PRAGMA foreign_keys=ON`）
- 修复创建 HTTP 客户端失败时爬虫直接崩溃的问题（降级为返回 `None` 并记录告警）
- 修复删除用户后其历史数据残留、无法彻底清理的问题（引入外键级联）
- 修复 `api_user_update` 的 `is_active` 布尔强转缺陷：字符串 `"false"` 会被误判为 `True`，现兼容 JSON 布尔与 `"true"` / `"1"` 等字符串
- 修复 `api_changes` / `api_compare` / `api_schedule_update` 对非法整型参数返回 500 的问题（改为 400）
- 修复健康检查 `/health` 的 `monitored_users` 数值失真（改为读取数据库活跃用户数）
- 修复数据库迁移错误被静默吞掉的问题（`except: pass` 改为分类记录日志，仅 `duplicate column` 幂等情形静默）
- 移除被 git 跟踪的敏感导出文件 `saves/cs2mon_export_*.cs2mon`，并重写历史清除其在全部提交中的残留

### Security

- 登录成功时清除对应 IP 的失败计数，避免误锁
- Web 登录 Cookie 的 `Secure` 标记改为按请求协议条件设置：HTTPS 启用、纯 HTTP（本地 / 内网）关闭，修复 HTTP 下 cookie 认证永久失效的问题
- 前端修复多处 XSS 注入面：统一 `esc()` 文本 / 属性转义、`jsonAttr()` 安全嵌入 onclick JSON、分类 Tab 改用 `data-cat` 属性传值
- 代理 URL 日志脱敏（`_redact_url`），避免 `user:pass@` 凭据写入日志
- 移除翻译表加载的 `importlib.exec_module` 动态代码执行路径（仅从 JSON 加载）

### Performance

- 快照写盘优化：库存内容未变化且无事件时，跳过全量压缩写盘，仅刷新元数据（覆盖每轮无变化轮询的常见路径）
- 依赖锁定 `requirements.lock` 支持可复现构建

## [2.0.0] - 2026-07-26

### Added

- **Web 仪表盘**：物品分类浏览（步枪 / 手枪 / 匕首 / 手套等）、物品图片、磨损色条 + 品质徽章 + StatTrak 标识、按名称搜索、按磨损 / 名称排序、物品详情弹窗（印花 / 磨损 / 图案种子 / 阶段）、一键跳转 Steam 市场
- **多用户管理**：添加 / 编辑 / 删除监控用户、回收站（软删除恢复 / 永久删除）、批量导入（支持 Steam64 ID / 社区链接 / SteamDT 链接）
- **监控功能**：定时爬取（默认 5 分钟）、存储单元活动检测（基于 `total_inventory_count` delta）、交换识别（同类型物品配对）、变化事件记录（`added` / `removed` / `modified` / `swapped`）、每日归档快照（保留 90 天）、历史归档对比
- **分层调度**：高频 / 中频 / 低频三级独立监控循环，每用户可单独设置监控频率（`api/frequency/*`）
- **双通道通知**：用户通知（Telegram / 钉钉 / Server酱）+ 管理员告警（Telegram / Webhook，支持连续失败阈值）
- **代理支持**：`auto` / `manual` / `hosts` / `none` 四种模式，兼容 Steam++ hosts 加速与自定义 SOCKS5 / HTTP 代理
- **数据导出 / 导入**：全量数据导出（版本化 `.cs2mon` 格式）与一键导入恢复
- **监控控制**：Web 一键启动 / 停止 / 立即抓取、健康检查（`/health`、`/ping`）、心跳机制
- **WebSocket 实时推送**：监控事件、归档、层级调度状态实时广播到前端
- **认证**：Web 访问密码（留空自动生成）、HMAC-SHA256 token、登录速率限制

### Changed

- 数据模型基于 pydantic 事件溯源（`ChangeEvent`），压缩快照以 zlib 存入 SQLite
- 抓取器采用游标分页，支持超大库存（单页 `page_size` 可调）

### Fixed

- （首个正式版本，无历史缺陷）
