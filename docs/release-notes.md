# 百合会归档助手 — 版本发布说明

> 版本：0.9.0 | 发布日期：2026-06-24

## 版本信息

| 项目 | 值 |
|------|-----|
| 版本号 | 0.9.0 |
| Python 要求 | >= 3.11 |
| MCP SDK | >= 1.27.2 |

---

## v0.9.0 调整

- WebUI 新增专门的 RAG 管理页，支持批量重建索引、单贴重建、分区筛选和本地检索调试
- 新增批量归档与批量 RAG 索引的 MCP / CLI 入口，便于一次性处理多个 `tid`
- 任务列表支持暂停 / 恢复，运行中、排队中与完成态的展示更清晰
- 版本、README、使用手册、API 文档和 Agent 文档同步更新
- 主版本号推进到 `0.9.0`

## v0.8.1 调整

- 旧的 `yamibo-forum` skill 收口为弃用别名，避免和 `yamibo-mcp` 双入口并存
- Hermes MCP 导入从旧的 SSE 语义切换为 `streamable-http` 的 `/mcp`
- `wait_for_job`、`yamibo://jobs/{job_id}/status`、`job_events` 的 Agent 文档同步更新
- 新增本地 RAG 检索管理页（`/rag`），可查看索引覆盖、创建 `rag_index` 任务并调试 keyword/vector/hybrid 检索
- Web API 新增 `/api/rag/overview`、`/api/rag/threads`、`/api/rag/index`、`/api/rag/search`
- 用户手册、部署说明、开发说明和 WebUI 设计文档同步补充 RAG 管理链路
- 主版本号推进到 `0.8.1`

## v0.8.0 新增功能

### Hermes Agent 回归基线

- 新增 `scripts/run_hermes_benchmark.sh` 和 `scripts/run_hermes_benchmark.py`，可重复执行真实 Hermes 黑盒回归
- 自动执行 7 张标准任务卡，覆盖只读发现、本地缺失恢复、异步启动、重复 job 复用、导出策略分流、多线程 fanout 和多 forum 读取
- 自动导出 Hermes session transcript，并解析为可打分的 Markdown / JSON 报告

### Job 运行态诊断增强

- `read_job` 返回新增 `execution_state`、`diagnostic_summary`、`needs_attention`
- 对长时间 `queued`、长时间 `running`、`interrupted`、`download_images` 阶段过慢等情况提供统一诊断摘要
- Agent 可直接据此区分正常推进、需关注和疑似卡住任务，再决定是否读取 `read_job_events`

### 测试与文档

- 新增 `tests/unit/test_benchmark/test_hermes.py`，覆盖 transcript 解析和评分逻辑
- Agent 验收、接口和测试策略文档同步补充 Hermes 自动化回归说明
- 主版本号推进到 `0.8.0`

## v0.7.1 新增功能

### 文档与分发物同步

- `docs/database-design.md` 与实际 schema 对齐，修正 `jobs.tid` 类型并补充 `threads.category`
- `dist/yamibo-mcp/SKILL.md` 与 `dist/yamibo-mcp/agents/openai.yaml` 同步到新的 Agent 接口命名和工作流资源说明
- 主版本号推进到 `0.7.1`

## v0.7.0 新增功能

### Agent 架构与兼容层收口

- 新增中文文档 `architecture-for-agents.md`，明确主数据流、目录职责、常见改动路径、legacy 禁区和测试矩阵
- `server/tools.py`、`server/protocol.py`、`server/resource_handlers.py` 以及 `application/*_use_cases.py` 明确标记为兼容层，不再作为新代码入口
- README 项目结构补充 `frontend/` 与 `src/yamibo_mcp/web/static/` 的源码/产物关系

### 静态资源与打包

- `frontend/` 作为前端源码目录继续保留
- `src/yamibo_mcp/web/static/` 继续作为 packaged static artifact，并补充目录说明
- wheel/sdist 明确包含 `yamibo_mcp.web.static/*`，避免安装后 Web 控制台缺失静态文件

### 文档与可维护性

- 全仓库主文档版本统一推进到 `0.7.0`
- Agent 接口、API、开发说明、部署说明、数据库设计等文档同步校正
- 文档语言继续以中文为主，移除英文架构导航的歧义

## v0.6.0 新增功能

### 轻小说专用更新链路

- 新增 `check_thread_updates` 只读接口，用于判断已归档轻小说是否有新内容
- 新增 `update_thread` 追加更新任务，仅在检测到远端变化时抓取新增楼层
- 追加更新使用只看楼主 URL、尾页楼层指纹和页数快照进行增量判断
- 轻小说 TXT 导出改为独立文件，可按楼层增量追加，导出目录可单独配置

### 富文本阅读与导出

- 阅读预览保留正文中的颜色、加粗、斜体、链接等富文本语义
- 导出仍然保持纯文本，不把论坛样式带入 TXT
- 过滤编辑提示、无关链接和装饰性富文本，降低脏内容

### WebUI 改进

- 贴子详情信息编辑支持标题，并让标题单独占一行
- 任务详情可查看部分成功的失败原因与成功项明细
- 阅读模式支持更多预设、自定义方案和字体大小控制
- 移动端阅读预览修复段落叠加和宽度利用问题

### 稳定性与配置

- `YAMIBO_NOVEL_TXT_EXPORT_DIR` 独立控制轻小说 TXT 导出目录
- `YAMIBO_NOVEL_AUTHOR_ONLY_MAX_PAGES` 与 `YAMIBO_NOVEL_AUTHOR_ONLY_PAGE_DELAY_SECONDS` 控制增量更新范围和请求间隔
- 更新检查和追加更新接口共用同一份只看楼主归档快照，避免重复全量同步

### 论坛版块与元数据

- 论坛注册继续覆盖漫画、轻小说、动漫和扩展分区
- 自动提取论坛链接、分类标签与作者信息
- 贴子详情与系列信息继续保持结构化返回

### 数据库与迁移

- 新增 `forums`、`content_blocks`、`assets`、`job_events` 表
- `threads` 表新增 `forum_id`、`content_kind`、`primary_media_type` 列
- 历史数据自动回填为 `forum_id=30`、`content_kind='comic'`、`primary_media_type='image'`

---

## 系统架构

系统由两个独立进程组成：

| 进程 | 入口 | 说明 |
|------|------|------|
| MCP Server | `yamibo-mcp-server stdio` | 由 LLM 客户端触发，随会话长期运行 |
| Daemon | `yamibo-daemon` | 独立后台进程，内嵌 Web 控制台 |

Web 控制台随 Daemon 自动启动（`http://127.0.0.1:8765`），无需单独运行。

---

## 功能清单

### 核心功能

- **MCP Server**：基于 FastMCP 的标准 MCP 服务器，支持 stdio/SSE/HTTP 传输
- **Daemon**：后台任务消费引擎，支持租约抢占、心跳、崩溃恢复
- **Web 控制台**：嵌入式 HTTP 管理界面（随 Daemon 启动），中英文双语
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

- 漫画/通用贴子 ZIP 打包（按系列分目录）
- 轻小说 TXT 导出（独立目录、支持增量追加）
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
| yamibo-daemon | daemon.main:main | 后台任务消费 + 嵌入式 Web 控制台 |
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
| 单帖子导出 | 不支持多帖子合并导出 |
| Windows 未测试 | 仅在 macOS/Linux 上验证 |

---

## 测试覆盖

| 类别 | 用例数 | 状态 |
|------|--------|------|
| 黑盒测试（解析器 fixture） | 212 | 全部通过 |
| 白盒测试（DB + Domain + Storage + Services） | 177 | 全部通过 |
| 白盒测试（Server + Application + Yamibo 模块） | 326 | 全部通过 |
| **总计** | **715** | **全部通过** |

执行时间：约 10s

详见 [测试报告](test-report.md) 和 [测试方案](testing-strategy.md)。

---

## 后续规划

- [x] 多论坛板块支持
- [ ] 多帖子合并导出
- [ ] 增量同步优化
- [ ] 图片 OCR 文字识别
- [ ] 系列目录自动生成
- [ ] Windows 兼容性测试
- [ ] CI/CD 流水线
