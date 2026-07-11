# 文档索引

`docs/` 同时承载产品说明、运维手册、架构设计、MCP 指南和阶段性实现报告。当前事实、后续规划与历史材料已在下面分组；请按阅读顺序使用，并把“历史实现报告”视为存档材料，而不是当前操作手册。

## 当前优先阅读

- [../README.md](../README.md)
  项目总览、快速开始、常用 CLI
- [user-manual.md](user-manual.md)
  面向日常使用者的操作手册
- [deployment-guide.md](deployment-guide.md)
  部署、备份、Daemon、MCP 集成
- [api-reference.md](api-reference.md)
  CLI / MCP / Web API 参考
- [development-guide.md](development-guide.md)
  面向开发者的代码与模块说明
- [release-readiness.md](release-readiness.md)
  1.0 发布门禁、可清理项目、已知限制与后续路线
- [llm-native-runtime-roadmap.md](llm-native-runtime-roadmap.md)
  Agent 友好型 LLM 原生运行时的后续架构、接口和实施阶段

## MCP / Agent 指南

- [agent-interface.md](agent-interface.md)
- [mcp-guides/agent-workflows.md](mcp-guides/agent-workflows.md)
- [mcp-guides/archive-model.md](mcp-guides/archive-model.md)
- [mcp-guides/error-codes.md](mcp-guides/error-codes.md)

说明：

- `agent-evaluation.md` 是完整验收标准的唯一真相源
- `mcp-guides/agent-evaluation.md` 只保留运行时速查和指向完整标准的入口

## 架构 / 设计 / 数据库

- [architecture-for-agents.md](architecture-for-agents.md)
- [llm-native-runtime-roadmap.md](llm-native-runtime-roadmap.md)
- [database-design.md](database-design.md)
- [product-requirements.md](product-requirements.md)
- [testing-strategy.md](testing-strategy.md)
- [webui-design.md](webui-design.md)
- [discussion-trend-v1.md](discussion-trend-v1.md)

## 历史 / 阶段性报告

这些文档保留是有价值的，但它们不是当前操作真相来源：

- [history/rag-phase1-4-summary.md](history/rag-phase1-4-summary.md)
- [history/rag-phase4-implementation-report.md](history/rag-phase4-implementation-report.md)
- [history/rag-sqlite-vec-design.md](history/rag-sqlite-vec-design.md)
- [history/fastapi-migration-plan.md](history/fastapi-migration-plan.md)

当前判断：

- `docs/README.md`、`user-manual.md`、`deployment-guide.md`、`api-reference.md`、`development-guide.md` 可以视为“当前文档”
- 以 `rag-phase*`、`sqlite-vec`、迁移 plan 命名的文件，应视为“历史 / 设计过程文档”
- 当前发布版本统一为 `1.0.0`；`docs/history/` 中的旧版本号作为历史记录保留

## 当前接口注意点

- 单帖归档的主命令是 `create-thread-archive-job`；`create-sync-thread-job` 仅作为 legacy alias 保留
- 批量归档当前使用 `create-sync-thread-batch-jobs`
- 历史 file-only archive 的批量重同步 / PG 回填流程已落文档
- `cleanup-orphan-thread-dirs` 对“磁盘有归档、DB 无 thread row”的旧归档并不安全，使用前必须先确认缺失 tid 已处理
