# 产品需求文档 (PRD) & 系统架构设计

> 版本：0.2.0 | 更新日期：2026-06-21

## 1. 产品概述

### 1.1 产品名称

Yamibo MCP — 百合会漫画本地归档系统

### 1.2 产品定位

面向 yamibo.com（百合会）论坛用户的本地归档工具。支持多论坛分区（漫画区、轻小说区、动漫区、水区），通过 MCP（Model Context Protocol）协议，使 LLM 客户端能够浏览、搜索、归档和导出论坛帖子。

### 1.3 目标用户

- 百合会论坛活跃用户
- 希望本地备份漫画帖子的用户
- 使用 MCP 客户端（如 Claude Desktop、Cursor 等）的 LLM 用户

### 1.4 核心价值

| 价值 | 说明 |
|------|------|
| 多分区支持 | 漫画区(30)、轻小说区(55)、动漫区(5)、水区(33)，通过 forum_id 切换 |
| 自动归档 | 自动抓取论坛帖子，解析标题、楼层、图片，生成结构化本地存档 |
| 智能标题解析 | 规则 + LLM 双重解析，提取汉化组、作者、漫画名、章节信息 |
| 内容类型 | 支持 comic/novel/discussion/mixed 四种内容形态 |
| 系列管理 | 自动按 series_key 聚合同一系列的多个章节帖子 |
| 标准化导出 | 生成包含 context.md + metadata.json + 图片的 ZIP 导出包 |
| Agent Resources | 紧凑 summary/diagnostics/posts/assets 资源，低 token 开销 |
| MCP 集成 | 通过标准 MCP 协议暴露工具和资源，LLM 可直接调用 |

---

## 2. 功能需求

### 2.1 论坛浏览

| ID | 需求 | 优先级 |
|----|------|--------|
| F-001 | 浏览指定论坛分区指定页码的帖子列表 | P0 |
| F-002 | 支持按标题搜索帖子（远端优先，本地兜底） | P0 |
| F-003 | 支持过滤置顶帖和公告帖 | P1 |
| F-004 | 支持多论坛分区（漫画区/轻小说区/动漫区/水区） | P0 |

### 2.2 帖子归档

| ID | 需求 | 优先级 |
|----|------|--------|
| F-010 | 通过 tid 或 URL 创建归档任务 | P0 |
| F-011 | 自动解析帖子标题（规则引擎 + LLM 辅助） | P0 |
| F-012 | 下载帖子内嵌图片到本地 | P0 |
| F-013 | 生成 context.md（Markdown 格式，含 YAML frontmatter） | P0 |
| F-014 | 生成 metadata.json（结构化元数据） | P0 |
| F-015 | 自动关联 series（系列管理） | P1 |
| F-016 | 支持批量归档（按页码范围） | P1 |
| F-017 | 校验归档完整性（图片、楼层） | P1 |
| F-018 | 内容类型分类（comic/novel/discussion/mixed） | P1 |

### 2.5 Agent 资源

| ID | 需求 | 优先级 |
|----|------|--------|
| F-050 | 帖子紧凑摘要资源（不读完整 context.md） | P0 |
| F-051 | 帖子诊断资源（归档状态、缺失资产、建议操作） | P0 |
| F-052 | 帖子内容块资源（有序 text/image/quote/link blocks） | P1 |
| F-053 | 帖子资产资源（图片/附件/共享资源状态） | P1 |
| F-054 | 论坛分区列表和摘要资源 | P1 |
| F-055 | 任务事件时间线资源 | P1 |

### 2.3 导出

| ID | 需求 | 优先级 |
|----|------|--------|
| F-020 | 将归档帖子导出为 ZIP 包 | P0 |
| F-021 | 支持三种导出策略：cache_only / sync_if_stale / force_resync | P1 |
| F-022 | 导出包按系列分目录组织 | P1 |

### 2.4 Web 控制台

| ID | 需求 | 优先级 |
|----|------|--------|
| F-030 | Dashboard 展示归档统计、最近任务、Daemon 状态 | P0 |
| F-031 | 任务列表与详情页（含 staging 排障） | P0 |
| F-032 | 帖子归档列表与详情页（含阅读预览、图片预览） | P0 |
| F-033 | 系列管理（查看、合并、确认） | P1 |
| F-034 | 标题复核（手动修正解析结果） | P1 |
| F-035 | 中英文双语界面 | P1 |

### 2.5 系统管理

| ID | 需求 | 优先级 |
|----|------|--------|
| F-040 | 数据库备份（带时间戳、自动轮转） | P0 |
| F-041 | 过期 staging 清理 | P1 |
| F-042 | 数据目录重置（调试用） | P2 |

---

## 3. 非功能需求

| 类别 | 要求 |
|------|------|
| 性能 | 单帖子归档 < 30s（不含图片下载） |
| 可靠性 | Daemon 崩溃后自动恢复中断任务 |
| 并发 | 支持多 Daemon 实例并行消费 |
| 安全 | Cookie 文件不入库、不入导出包 |
| 兼容性 | Python >= 3.11，SQLite >= 3.35 |

---

## 4. 系统架构

### 4.1 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                        LLM Client                               │
│              (Claude Desktop / Cursor / 自定义)                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ MCP Protocol (stdio/SSE)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    yamibo-mcp-server                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │  Tools   │  │Resources │  │ Protocol │  │  Schemas │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Application Layer (ensure_thread, archive_thread_job)   │  │
│  └──────────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ SQLite (jobs + job_events)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     yamibo-daemon                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │  Runner  │  │ Recovery │  │ Handlers │  │  Staging │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
└────────┬──────────────┬──────────────┬──────────────────────────┘
         │              │              │
         ▼              ▼              ▼
┌────────────┐  ┌────────────┐  ┌────────────┐
│   SQLite   │  │  Yamibo    │  │  Storage   │
│  forum.db  │  │  Forum     │  │  Files     │
└────────────┘  └────────────┘  └────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     yamibo-web (嵌入式)                          │
│              HTTP 控制台，随 Daemon 启动                          │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 核心组件

| 组件 | 入口 | 职责 |
|------|------|------|
| MCP Server | `yamibo-mcp-server stdio` | 接收 LLM 客户端请求，创建任务到 SQLite |
| Daemon | `yamibo-daemon` | 轮询 SQLite，抢占任务并执行处理器 |
| Web Console | 嵌入 Daemon | HTTP 管理界面 |
| Yamibo Client | `yamibo/client.py` | 论坛 HTTP 客户端，含登录、Cookie 管理 |
| Parsers | `yamibo/parsers/` | HTML 解析（帖子详情、列表页、搜索结果） |
| Title Parser | `yamibo/title/parser.py` | 标题规则引擎 |
| Title LLM | `services/title_llm.py` | LLM 辅助标题解析 |
| Storage | `storage/` | 文件 I/O（staging、归档、导出、图片） |
| DB | `db/` | SQLite 连接、迁移、Repository |

### 4.3 Job 生命周期

```
创建 (Server/Web)
  │
  ▼
QUEUED ──→ Daemon acquire ──→ RUNNING
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
               SUCCEEDED     PARTIAL       FAILED
                                 │            │
                                 ▼            ▼
                            (可人工排查)   (可重试)
                                 │
                                 ▼
                           INTERRUPTED ──→ Daemon 恢复 ──→ RUNNING
```

**状态说明**：

| 状态 | 含义 |
|------|------|
| `queued` | 已创建，等待 Daemon 消费 |
| `running` | Daemon 已抢占，正在执行 |
| `succeeded` | 全部成功 |
| `partial` | 帖子已归档，但部分图片缺失 |
| `failed` | 执行失败，含 error_code 和 error_message |
| `interrupted` | Daemon 超时，可被其他 Daemon 恢复 |
| `retrying` | 准备重试 |
| `cancelled` | 已取消 |

### 4.4 数据流

```
论坛 HTML
  │
  ▼
page_classifier → 判断页面类型
  │
  ▼
thread_detail parser → 提取楼层、图片 URL
  │
  ▼
title parser (规则) → 提取标题结构
  │
  ▼
title LLM (可选) → 低置信度时二次提取
  │
  ▼
validate_thread_snapshot → 校验完整性
  │
  ▼
download_images_to_staging → 图片下载到 staging
  │
  ▼
upsert_snapshot → 写入 SQLite（threads/floors/title_parse/series/FTS）
  │
  ▼
materialize_thread → 写入正式归档目录（context.md + metadata.json + images）
  │
  ▼
export_thread_zip → 打包导出（可选）
```

### 4.5 技术栈

| 层次 | 技术 |
|------|------|
| 语言 | Python >= 3.11 |
| 包管理 | uv (setuptools build backend) |
| MCP SDK | `mcp` >= 1.27.2 (FastMCP) |
| 数据库 | SQLite (WAL mode, FTS5) |
| HTTP 客户端 | urllib (stdlib) |
| HTML 解析 | html.parser (stdlib, 纯正则) |
| Web 服务器 | http.server.ThreadingHTTPServer (stdlib) |
| LLM 集成 | OpenAI-compatible API (urllib 直调) |
| 测试框架 | pytest >= 8.0 |

---

## 5. 目录结构

```
yamibo/
├── pyproject.toml              # 项目配置、依赖、入口点
├── yamibo.local.json           # 本地配置（不提交）
├── .cookie                     # 论坛认证 Cookie
├── src/yamibo_mcp/
│   ├── config.py               # Settings 数据类 + 加载逻辑
│   ├── errors.py               # 自定义异常层次
│   ├── logging.py              # 日志配置
│   ├── time_utils.py           # UTC 时间工具
│   ├── server/                 # MCP/CLI 适配层
│   │   ├── app.py              # 轻量入口委托
│   │   ├── mcp_registry.py     # FastMCP tool/resource 注册
│   │   ├── cli.py              # CLI 参数解析与分发
│   │   ├── agent_tools.py      # Agent-facing tool 适配
│   │   ├── resource_handlers.py # Resource 读取处理
│   │   ├── legacy_tools.py     # 旧工具名兼容包装
│   │   ├── legacy_protocol.py  # JSON-RPC 兼容层
│   │   ├── tools.py            # legacy re-export
│   │   ├── resources.py        # URI 模板
│   │   └── schemas.py          # 响应结构构建
│   ├── application/          # 应用层
│   │   ├── contracts.py      # Agent Contract (AgentResponse)
│   │   ├── thread_use_cases.py # 帖子用例（ensure_thread, archive_thread_job）
│   │   └── job_use_cases.py  # 任务用例（get_job_status_payload）
│   ├── daemon/                 # 后台任务消费
│   │   ├── main.py             # Daemon 入口
│   │   ├── runner.py           # 轮询 + 抢占 + 执行循环
│   │   ├── recovery.py         # 过期任务恢复
│   │   └── handlers/           # 任务处理器
│   │       ├── sync_thread.py  # 帖子同步
│   │       ├── export_thread.py # 帖子导出
│   │       ├── cleanup_job.py  # 清理
│   │       ├── title_refine.py # 标题精炼
│   │       └── noop.py         # 空操作
│   ├── web/                    # Web 控制台
│   │   └── app.py              # HTTP Handler + 嵌入式服务器
│   ├── yamibo/                 # 论坛交互层
│   │   ├── client.py           # HTTP 客户端（Cookie、登录、重试）
│   │   ├── page_classifier.py  # 页面类型识别
│   │   ├── urls.py             # URL 构建与规范化
│   │   ├── series_matcher.py   # 系列匹配决策
│   │   ├── parsers/            # HTML 解析器
│   │   │   ├── common.py       # 共用工具
│   │   │   ├── forum_list.py   # 列表页解析
│   │   │   ├── thread_detail.py # 帖子详情解析
│   │   │   └── search_results.py # 搜索结果解析
│   │   ├── title/              # 标题解析
│   │   │   ├── parser.py       # 规则引擎
│   │   │   └── normalizer.py   # 文本规范化
│   │   └── cleaners/           # 内容清洗
│   │       └── content_cleaner.py
│   ├── db/                     # 数据层
│   │   ├── connection.py       # SQLite 连接管理
│   │   ├── migrations.py       # Schema DDL + 迁移
│   │   └── repositories/       # Repository 模式
│   │       ├── jobs.py         # Job CRUD + 租约抢占
│   │       ├── threads.py      # 帖子 + 标题 + 楼层 + FTS
│   │       ├── series.py       # 系列管理
│   │       ├── audit_events.py # 审计事件
│   │       ├── content_blocks.py # 内容块
│   │       ├── assets.py       # 资产管理
│   │       └── job_events.py   # 任务事件 outbox
│   ├── services/               # 外部服务集成
│   │   ├── llm_client.py       # OpenAI-compatible LLM 客户端
│   │   ├── title_llm.py        # LLM 标题解析
│   │   └── title_hints.py      # 标题提示词管理
│   ├── storage/                # 文件 I/O
│   │   ├── paths.py            # 存储路径模板
│   │   ├── atomic.py           # 原子写入
│   │   ├── staging.py          # staging 快照/失败日志
│   │   ├── thread_archive.py   # 正式归档物化
│   │   ├── markdown.py         # Markdown + JSON 渲染
│   │   ├── images.py           # 图片下载 + 分类
│   │   └── exports.py          # ZIP 导出
│   ├── domain/                 # 领域模型
│   │   ├── models.py           # Job / ThreadSnapshot / ContentBlock / AssetSnapshot / PostSnapshot
│   │   ├── enums.py            # JobStatus / JobType
│   │   ├── forums.py           # ForumProfile / resolve_forum
│   │   ├── content.py          # classify_content_kind / validate_by_profile / build_content_snapshot
│   │   ├── job_state.py        # Job ID 生成
│   │   └── validation.py       # 快照校验
│   └── maintenance/            # 维护命令
│       ├── backup_db.py        # 数据库备份
│       ├── cleanup_data.py     # 过期清理
│       └── reset_data.py       # 数据重置
├── tests/                      # 测试
│   ├── conftest.py
│   ├── fixtures/               # 测试数据
│   └── unit/                   # 单元测试
├── scripts/                    # 辅助脚本
├── data/                       # 运行时数据（不提交）
└── docs/                       # 文档
```
