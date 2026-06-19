# 版本发布说明

> 版本：0.1.0 | 发布日期：2026-06-19

## 版本信息

| 项目 | 值 |
|------|-----|
| 版本号 | 0.1.0 |
| Python 要求 | >= 3.11 |
| MCP SDK | >= 1.27.2 |
| 测试用例数 | 429（全部通过） |

---

## 系统架构

系统由两个独立进程组成：

| 进程 | 入口 | 说明 |
|------|------|------|
| MCP Server | `yamibo-mcp-server stdio` | 由 LLM 客户端触发，随会话长期运行 |
| Worker | `yamibo-worker` | 独立后台进程，内嵌 Web 控制台 |

Web 控制台随 Worker 自动启动（`http://127.0.0.1:8765`），无需单独运行。

---

## 功能清单

### 核心功能

- **MCP Server**：基于 FastMCP 的标准 MCP 服务器，支持 stdio/SSE/HTTP 传输
- **Worker**：后台任务消费引擎，支持租约抢占、心跳、崩溃恢复
- **Web 控制台**：嵌入式 HTTP 管理界面（随 Worker 启动），中英文双语
- **CLI**：所有工具均可通过命令行直接调用

### 论坛交互

- 论坛列表页浏览（按页码）
- 帖子搜索（远端优先，本地 FTS 兜底）
- 帖子详情抓取（含自动登录、Cookie 管理）
- 批量同步（按页码范围）
- 页面类型识别（帖子详情/列表页/搜索结果/登录页/维护页）

### 标题解析

- 规则引擎解析：汉化组、作者、核心标题、章节信息
- LLM 辅助解析：低置信度时自动调用 OpenAI-compatible API
- 标题提示词系统：自动积累汉化组和作者名
- 章节索引计算：支持范围章节（51~60）、前/后篇、其N 后缀、特典/彩页/番外（→999）
- 冒号模式：`33:标题` → chapter_name="33", chapter_title="标题"
- 无章节默认值：chapter_name="1", chapter_index=1.0
- 文本规范化：NFKC、繁简转换、标点统一

### 归档系统

- 帖子 HTML 解析（楼层、图片 URL、发布者、时间）
- 图片下载（支持重试、格式检测、共享资源去重）
- 内容清洗（去附件提示、去噪声文本）
- 校验系统（楼层完整性、图片标记一致性）
- 三级归档状态：complete / partial / stale

### 系列管理

- 自动系列聚合（基于 series_key + creator_key）
- 别名累积
- 系列合并
- 人工复核

### 导出系统

- ZIP 打包（按系列分目录）
- 三种导出策略：cache_only / sync_if_stale / force_resync
- 导出前完整性检查

### 存储系统

- 原子文件写入
- Staging 隔离（每个任务独立目录）
- SQLite WAL 模式
- FTS5 全文搜索

### 运维工具

- 数据库备份（带时间戳、自动轮转）
- 过期 staging 清理
- 数据目录重置
- 审计事件记录

---

## 技术架构

### 依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| mcp | >= 1.27.2 | MCP SDK (FastMCP) |
| pytest | >= 8.0 | 测试框架（dev） |
| setuptools | >= 68 | 构建后端 |

所有其他依赖（urllib、sqlite3、html.parser、http.server）均为 Python 标准库。

### 入口点

| 命令 | 模块 | 说明 |
|------|------|------|
| yamibo-mcp-server | server.app:main | MCP 服务器 + CLI（由 LLM 客户端触发） |
| yamibo-worker | worker.main:main | 后台任务消费 + 嵌入式 Web 控制台 |
| yamibo-init-db | db.migrations:main | 数据库初始化 |
| yamibo-backup-db | maintenance.backup_db:main | 数据库备份 |
| yamibo-maintenance-cleanup | maintenance.cleanup_data:main | 过期清理 |
| yamibo-reset-data | maintenance.reset_data:main | 数据重置 |

---

## 已知限制

| 限制 | 说明 |
|------|------|
| 论坛维护窗口 | 每天 5:30-6:30（UTC+8）无法访问论坛 |
| 搜索限流 | 论坛搜索接口约 10 秒/次 |
| 仅支持漫画区 | 当前仅支持 forum-30（中文百合漫画区） |
| 单帖子导出 | 不支持多帖子合并导出 |
| Windows 未测试 | 仅在 macOS/Linux 上验证 |

---

## 测试覆盖

| 类别 | 用例数 | 状态 |
|------|--------|------|
| 黑盒测试（解析器 fixture） | 212 | 全部通过 |
| 白盒测试（DB + Domain + Storage + Services） | 142 | 全部通过 |
| 白盒测试（Yamibo 模块 + 规则引擎直接） | 75 | 全部通过 |
| **总计** | **429** | **全部通过** |

执行时间：0.84s

详见 [测试报告](test-report.md) 和 [测试方案](testing-strategy.md)。

---

## 后续规划

- [ ] 多论坛板块支持
- [ ] 多帖子合并导出
- [ ] 增量同步优化
- [ ] 图片 OCR 文字识别
- [ ] 系列目录自动生成
- [ ] Windows 兼容性测试
- [ ] CI/CD 流水线
