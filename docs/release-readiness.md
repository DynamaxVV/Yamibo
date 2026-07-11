# 1.0 发布门禁与后续路线

> 版本：1.0.0 | 更新日期：2026-07-12

本文是 1.0 发布范围的唯一清单。当前操作事实仍以用户手册、部署指南、API 参考和 Agent 接口文档为准。

## 发布阻断项

以下项目必须在创建 1.0 tag 前全部满足：

- `pyproject.toml`、Python 包、FastAPI 和前端版本均为 `1.0.0`
- `uv run pytest` 全量通过，PostgreSQL 用例不能因缺少测试数据库而跳过
- `npm --prefix c run build` 通过
- `docker compose config --quiet` 和生产镜像构建通过
- 从空 PostgreSQL 数据库执行 Alembic 升级成功
- 使用实际备份完成一次隔离恢复演练
- Web、MCP 和 PostgreSQL 默认只绑定 loopback；公网访问必须经过 TLS 和访问控制
- `.env`、cookie、数据库 dump、schema 快照及本地 Agent 状态不进入 Git 或镜像
- 前端目录迁移完整提交，不保留 `frontend/` 删除与 `c/` 未跟踪并存的状态
- `AGENTS.md` 与 `.yamibo/chat` 作为项目级 Agent 行为契约完成审阅并纳入版本；会话状态、回放和个人配置不得混入

## 可以直接清理

- 本地 `.env`、数据库 dump、临时 schema 导出和 `migration/` 工作目录
- `.omc/`、`.mimocode/`、`.tokensave/`、`.serena/`、编辑器配置等本地状态
- 已被 Vite 新构建替代的旧静态 hash 资产
- 文档中的过期目录名、SQLite-only 描述和失效命令
- 根文档与 MCP guide 的重复说明；根文档保留完整规范，guide 保留运行时摘要和链接

清理只针对版本控制和发布包，不自动删除本地备份或用户数据。

## 1.0 保留的已知限制

- Web 控制台和 MCP 服务本身不提供完整的互联网身份认证；默认 loopback 是安全边界
- daemon 与嵌入式 Web 仍可运行在同一进程
- 文件归档依赖本地或共享文件系统，不支持无状态多节点 worker
- SQLite 仅用于本地轻量模式；Discussion Trend 仍为 PostgreSQL-only
- Web Chat 是 OpenAI-compatible 对话入口，不是内置 MCP tool-loop Agent

## 1.x 优先改进

1. 为 Web/MCP 增加统一认证、权限、审计和速率限制。
2. 将数据库迁移从请求路径和应用启动职责中拆成独立部署 Job。
3. 统一 MCP、Web 和 CLI 的 `AgentResult`、错误和资源契约。
4. 建立机器可读 capability manifest，声明副作用、幂等性、错误码和后继工具。
5. 将 Web Chat 接入受控 tool loop、Job 状态机和 evidence citation。
6. 增加真实备份恢复、长任务中断恢复和 Agent transcript 的持续回归。

Agent/LLM 运行时的目标分层、契约和验收阶段以 [Agent 友好型 LLM 原生运行时路线图](llm-native-runtime-roadmap.md) 为准。本节只保留发布优先级摘要，避免在发布门禁文档中维护第二套设计。

## 2.0 候选范围

- 对象存储与无状态 worker
- 多租户和细粒度数据权限
- append-only 采集快照、回复关系图和可复现数据挖掘管线
- 多 Agent 协作、预算控制和模型路由

这些项目不应阻塞 1.0，也不应以未稳定的实验接口进入 1.0 公共契约。
