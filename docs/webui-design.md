# YamiboMCP WebUI 功能与设计文档

> 版本：0.2.0 | 更新日期：2026-06-21

## 1. 技术架构

### 1.1 前端

- **框架**：React 18 + TypeScript
- **构建**：Vite 6
- **路由**：react-router-dom v6
- **样式**：纯 CSS Custom Properties（无 CSS-in-JS、无 Tailwind）
- **字体**：Space Grotesk（本地 woff2）、系统字体栈

### 1.2 后端

- **API 层**：`src/yamibo_mcp/web/api.py` — 基于 Python http.server 的 JSON API
- **静态服务**：`src/yamibo_mcp/web/app.py` — 生产模式下由 Python 服务器同时提供 API 和静态文件
- **开发模式**：Vite dev server 代理 `/api` 到 Python 后端

### 1.3 构建产物

```
frontend/
├── src/
│   ├── api/client.ts          # 类型安全的 fetch wrapper
│   ├── themes/                # 4 套主题定义
│   ├── context/ThemeContext.tsx # 主题 provider + localStorage
│   ├── components/            # 共享组件
│   └── pages/                 # 10 个页面
├── public/fonts/              # Space Grotesk woff2
└── vite.config.ts
```

构建后输出到 `src/yamibo_mcp/web/static/`，由 Python 服务器在生产模式下直接服务。

---

## 2. 页面功能清单

### 2.1 控制台 (`/`)

| 功能 | 说明 |
|------|------|
| 统计行 | 贴子数、系列数、导出数 |
| 最近任务表 | ID、类型、状态、阶段、TID、更新时间 |
| Worker 心跳表 | 仅显示有运行中任务的 worker |
| 审计事件表 | 操作、目标、执行者、时间 |
| 最近归档表 | TID、标题、归档状态、同步时间 |

### 2.2 任务列表 (`/jobs`)

| 功能 | 说明 |
|------|------|
| 状态筛选 | segmented control：全部/排队中/运行中/成功/失败/中断 |
| 创建任务 | 空任务按钮 + 同步任务表单（HTML 路径/TID/URL） |
| 任务表 | ID、类型、状态(badge)、阶段、TID、进度、错误、创建时间 |
| 自动刷新 | 有活跃任务时每 3 秒刷新 |

### 2.3 任务详情 (`/jobs/:id`)

| 功能 | 说明 |
|------|------|
| 诊断信息 | snapshot/failure/title_parse_log 存在性提示 |
| 详情表 | 完整 job 字段（ID、类型、状态、阶段、TID、进度、Worker、错误、时间、Payload、Artifacts） |
| 事件时间线 | job_events 表数据，按 event_id 递增 |
| Staging 文件链接 | snapshot.json / failure.json / title_parse_log.json |

### 2.4 贴子列表 (`/threads`)

| 功能 | 说明 |
|------|------|
| 搜索 | 按标题、作者、汉化组、发布者搜索 |
| 版块筛选 | 下拉选择全部版块或指定版块 |
| 日期筛选 | 全部时间/一天内/三天内/一周内/一月内/三月内 |
| 列表列 | TID、标题、版块、内容类型(badge)、发布时间(UTC+8)、同步时间(UTC+8) |
| URL 参数 | 支持 `?forum_id=X` 预选版块（从版块页面跳转） |

### 2.5 贴子详情 (`/threads/:tid`)

| 功能 | 说明 |
|------|------|
| 操作栏 | 返回列表（上下文感知：从系列进入显示"返回系列列表"）、重新同步、导出、删除（二次确认） |
| 归档信息表 | TID、原贴网址（可点击）、原始标题、发布者、版块名、内容类型(colored badge)、归档状态、校验状态、图片数、路径 |
| 阅读预览 | 两栏布局：左 30% 楼层元数据（楼层号、发布者、时间、PID），右 70% 内容区 |
| 图片显示 | 内容图片流式展示，支持 50%/75%/100% 宽度切换（默认 75%，相对右侧 70% 列） |
| 非漫画图片 | 表情包等小图以 1.8em 高度内联显示 |
| 文字前置 | 每层楼先显示文字，再显示图片 |
| 内容块表 | 从 content_blocks 表读取（如有数据） |
| 发布者样式 | 加粗 + 强调色，与正文区分 |
| 回退检测 | 通过 `location.state.from` 检测来源页面 |

### 2.6 系列列表 (`/series`)

| 功能 | 说明 |
|------|------|
| 搜索 | 按系列名、系列键、作者客户端过滤 |
| 复核筛选 | 全部/待复核/已确认 |
| 列表列 | ID、标题、作者、系列键、贴子数、复核状态 |
| 无匹配提示 | "无匹配系列" |

### 2.7 系列详情 (`/series/:id`)

| 功能 | 说明 |
|------|------|
| 操作栏 | 返回系列列表、删除系列（仅空系列可删，二次确认） |
| 系列信息表 | ID、系列键、作者 |
| 贴子表 | TID、标题、章节、归档状态（链接传递 `state={{ from: 'series' }}`） |

### 2.8 复核 (`/review`)

| 功能 | 说明 |
|------|------|
| 贴子标题复核 | 表格：TID、标题、核心标题、作者、系列键、操作（编辑/确认） |
| 标题编辑 | 内联表单（标题、作者、核心标题、系列键） |
| 系列复核 | 表格：ID、标题、作者、系列键、贴子数、操作（编辑/确认/合并） |
| 系列编辑 | 内联表单（系列名、系列键、作者） |
| 合并推荐 | 每个系列自动查找相似系列（按 series_key 前缀匹配），显示为可点击标签 |
| 二次确认 | 所有编辑/确认/合并操作弹出确认对话框，显示修改前后对比 |
| 错误反馈 | 确认对话框内显示错误信息，失败时保持打开 |
| 批量重算 | "批量重算系列"按钮 |

### 2.9 导出 (`/exports`)

| 功能 | 说明 |
|------|------|
| 列表列 | TID、标题、归档状态、导出路径、操作（重新同步/创建导出） |

### 2.10 版块 (`/forums`)

| 功能 | 说明 |
|------|------|
| 列表列 | ID、名称、内容类型(colored badge)、贴子数、启用状态、操作 |
| 跳转操作 | "查看贴子"链接跳转到 `/threads?forum_id=X` |

### 2.11 日志 (`/logs`)

| 功能 | 说明 |
|------|------|
| 实时日志流 | 每 2 秒轮询 `/api/logs`，显示服务端运行日志 |
| 控制按钮 | 刷新、暂停/继续、清空 |
| 自动滚动 | 默认开启，可关闭 |
| 格式 | 精确到毫秒的时间戳 + 级别（颜色区分）+ 日志内容 |
| 等宽字体 | monospace 显示 |

---

## 3. 主题系统

### 3.1 架构

- `Theme` 接口定义颜色、字体、圆角、阴影等 30+ 个 CSS 变量
- `ThemeProvider` 通过 React Context 注入当前主题
- `localStorage` 持久化用户选择
- CSS 通过 `var(--xxx)` 引用主题变量

### 3.2 四套主题

| 主题 | 名称 | 字体 | 色调 | 圆角 | 阴影 |
|------|------|------|------|------|------|
| 极简专业 | minimalist | Outfit + Space Grotesk | 白底蓝强调 | 4-8px | 微弱 |
| 理性数据 | rational | Instrument Serif + Space Grotesk + JetBrains Mono | 灰底深蓝 | 2-4px | 无 |
| 粗野 | brutalist | Courier New monospace | 纯白纯黑红强调 | 0px | 无 |
| 复古 1957 | retro | Georgia serif | 暖黄底砖红强调 | 4-12px | 有 |

### 3.3 内容类型颜色

| 类型 | 样式类 | 颜色 |
|------|--------|------|
| 漫画 | `.badge-comic` | 蓝底深蓝字 `#e3f2fd/#1565c0` |
| 小说 | `.badge-novel` | 浅绿底深绿字 `#e8f5e9/#2e7d32` |
| 讨论 | `.badge-discussion` | 浅橙底深橙字 `#fff3e0/#e65100` |
| 混合 | `.badge-mixed` | 浅紫底深紫字 `#f3e5f5/#7b1fa2` |

### 3.4 状态 Badge 颜色

| 状态 | 类 | 颜色 |
|------|-----|------|
| succeeded/complete/valid | `.badge-ok` | 绿 |
| running/queued/pending | `.badge-accent` | 蓝 |
| partial | `.badge-warn` | 黄 |
| failed/error/missing | `.badge-error` | 红 |
| 其他 | `.badge-muted` | 灰 |

---

## 4. 组件设计

### 4.1 Layout

- 顶部栏：品牌名（20px bold）+ 导航链接（分隔线区分）+ 主题切换下拉
- 品牌名"百合会归档"与导航之间有 `border-left` 分隔
- 底部固定"顶部"浮动按钮

### 4.2 按钮风格

| 类 | 用途 | 样式 |
|-----|------|------|
| `.btn-subtle` | 次要操作 | 透明底，浅灰边框 |
| `.btn-primary` | 确认/保存 | 蓝色实心 |
| `.btn-danger-outline` | 删除（未确认） | 红色空心边框 |
| `.btn-danger` | 删除（已确认） | 红色实心 |
| `.btn-chip` | 推荐标签 | 药丸形，灰底 |

### 4.3 确认对话框

- 半透明遮罩 + 模糊背景
- 左右两栏对比：修改前 → 修改后
- 支持错误信息显示
- 点击遮罩关闭

### 4.4 筛选栏

- `.filter-bar` 容器：搜索框(flex:1) + 筛选下拉组 + 搜索按钮
- 一行展示，风格统一

### 4.5 两栏阅读布局

- `.floor-sidebar`（30%）：楼层号、发布者（加粗强调色）、时间、PID
- `.floor-content`（70%）：文字 → 内容图片 → 小图
- 图片宽度通过 `--img-pct` CSS 变量控制
- 响应式：移动端自动切换为上下布局

---

## 5. API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard` | 控制台数据 |
| GET | `/api/jobs` | 任务列表（可选 `?status=`） |
| GET | `/api/jobs/:id` | 任务详情 |
| GET | `/api/jobs/:id/events` | 任务事件 |
| GET | `/api/threads` | 贴子列表（`?q=&forum_id=&days=`） |
| GET | `/api/threads/:tid` | 贴子详情 |
| GET | `/api/threads/:tid/assets` | 贴子资产 |
| GET | `/api/threads/:tid/blocks` | 贴子内容块 |
| GET | `/api/threads/:tid/images` | 贴子图片（从 metadata.json） |
| GET | `/api/series` | 系列列表 |
| GET | `/api/series/:id` | 系列详情 + 关联贴子 |
| GET | `/api/series/:id/similar` | 相似系列推荐 |
| GET | `/api/forums` | 版块列表（含贴子计数） |
| GET | `/api/exports` | 导出列表 |
| GET | `/api/review` | 待复核标题 + 系列 |
| GET | `/api/debug/info` | 系统信息 + 统计 |
| GET | `/api/logs` | 实时日志（`?limit=&since=`） |
| POST | `/api/review/confirm-title` | 确认标题 |
| POST | `/api/review/confirm-series` | 确认系列 |
| POST | `/api/review/merge-series` | 合并系列 |
| POST | `/api/review/update-title` | 更新标题 |
| POST | `/api/review/update-series` | 更新系列 |
| POST | `/api/review/rebuild-series` | 批量重算系列 |
| POST | `/api/threads/resync` | 重新同步贴子 |
| POST | `/api/threads/export` | 导出贴子 |
| POST | `/api/threads/delete` | 删除贴子 |
| POST | `/api/series/delete` | 删除系列 |

---

## 6. 数据类型

| 接口 | 字段数 | 说明 |
|------|--------|------|
| `JobSummary` | 13 | 任务摘要 |
| `JobEvent` | 7 | 任务事件 |
| `ThreadSummary` | 17 | 贴子摘要（含 forum_id、content_kind、pub_time） |
| `ThreadDetail` | 21 | 贴子详情（含 url、floors、image_count） |
| `FloorSummary` | 6 | 楼层摘要 |
| `Asset` | 9 | 资产 |
| `ContentBlock` | 7 | 内容块 |
| `SeriesSummary` | 7 | 系列摘要 |
| `Forum` | 5 | 版块 |
| `ThreadImage` | 5 | 图片（含 is_content、is_shared） |
| `LogEntry` | 4 | 日志条目 |

---

## 7. 设计决策

| 决策 | 理由 |
|------|------|
| 纯 CSS Custom Properties | 零依赖，主题切换即时生效，浏览器兼容性好 |
| 4 套主题 | 满足不同审美偏好，localStorage 持久化 |
| 数据表格优先 | 信息密度高于卡片布局，适合管理型 UI |
| 两栏阅读布局 | 左侧元数据固定，右侧内容流式，模拟原论坛阅读体验 |
| 图片宽度可调 | 漫画/小说/讨论场景对图片展示需求不同 |
| 二次确认对话框 | 防止误操作，显示修改前后对比 |
| 实时日志轮询 | 简单可靠，无需 WebSocket 基础设施 |
| 发布者加粗强调色 | 在楼层信息中快速识别发布者身份 |
| 内容类型彩色 badge | 视觉上快速区分漫画/小说/讨论/混合 |
