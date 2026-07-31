---
kind: frontend_style
name: 前端样式系统 — 单文件内联 CSS + CSS 变量主题
category: frontend_style
scope:
    - '**'
source_files:
    - src/web/static/index.html
---

该项目的 Web 界面采用**单文件内联样式**方案，所有 HTML、CSS 与 JavaScript 均集中在 `src/web/static/index.html` 一个文件中，通过 `<style>` 标签直接嵌入 CSS，并通过 `<script>` 标签嵌入前端逻辑。没有使用任何前端构建工具、CSS 框架或组件库。

### 1. 使用的系统与工具
- **纯原生前端**：HTML5 + 内联 CSS + 原生 JavaScript（ES6+），无 React/Vue/Angular 等框架。
- **CSS 自定义属性（CSS Variables）**：通过 `:root` 定义全局设计令牌，包括背景色、卡片色、边框色、强调色、语义色（绿/红/橙）、文字层级色以及 CS2 物品品质专用色（fn/mw/ft/ww/bs）。
- **响应式布局**：使用 CSS Grid 和 Flexbox 实现自适应网格（`auto-fill, minmax(160px, 1fr)`）与移动端适配（`@media (max-width: 768px)`）。
- **WebSocket 实时通信**：前端通过 `new WebSocket()` 连接后端 `/api/ws` 获取实时状态更新。

### 2. 核心文件与位置
- **唯一前端入口**：`src/web/static/index.html`（约 1184 行，包含全部 UI 结构、样式与交互逻辑）
- **Web 服务路由**：`src/web/app.py` 提供静态文件服务与 API 端点
- **Dockerfile** 将 `src/web/static/index.html` 作为静态资源打包进容器

### 3. 架构与设计约定
- **单页应用（SPA）模式**：通过 JavaScript 动态渲染 DOM（`innerHTML` 拼接），无页面跳转，所有视图切换由 JS 控制。
- **深色主题设计**：默认使用暗色系配色（`--bg: #0f1117`, `--card: #1a1d27`），符合 Steam/CS2 社区审美。
- **模块化 CSS 类命名**：采用 BEM-like 命名风格（如 `.header`、`.sidebar`、`.item-card`、`.modal-detail`、`.stat-card`），按功能区域组织。
- **CS2 物品视觉映射**：内置磨损度颜色映射（Factory New → 蓝色、Battle-Scarred → 紫色）与稀有度颜色映射（★ → 金色、Covert → 红色）。
- **组件化 UI 元素**：按钮（`.btn-primary/.btn-success/.btn-danger/.btn-ghost`）、徽章（`.badge-ok/.badge-fail/.badge-off`）、面板（`.panel`）、模态框（`.modal-overlay`）等可复用样式。

### 4. 约束与规范
- **样式组织**：所有 CSS 必须写在 `<style>` 标签内，禁止外部 CSS 文件（仓库中无独立 `.css` 文件）。
- **主题扩展**：新增颜色需先在 `:root` 中定义 CSS 变量，再在组件中使用 `var(--xxx)` 引用。
- **响应式断点**：统一使用 `768px` 作为移动端适配断点。
- **字体栈**：统一使用 `'Segoe UI', system-ui, sans-serif` 字体族。
- **动画与过渡**：统一使用 `transition: all .2s` 或 `transition: all .15s` 的缓动时长。
- **滚动条定制**：通过 `::-webkit-scrollbar` 伪元素统一滚动条样式（宽度 6px，圆角 3px）。
- **认证机制**：前端通过 `Authorization: Bearer <token>` 头传递 JWT token，存储在 `localStorage.auth_token` 中。
- **API 调用规范**：所有后端请求通过封装的 `api(method, path, body)` 函数发起，自动附加认证头并处理 401 未授权状态。