# 文档索引

`docs/` 目前同时承载了产品说明、运维手册、架构设计、MCP 指南和阶段性实现报告。内容并不算混乱，但已经出现两类问题：

1. 面向用户的当前文档与阶段性报告混放，入口不够清晰
2. 少数命令示例已经落后于当前 CLI

建议按下面的阅读顺序使用，并把“历史实现报告”视为存档材料，而不是当前操作手册。

## 当前优先阅读

- [../README.md](/Users/vv/Code/Yamibo/README.md)  
  项目总览、快速开始、常用 CLI
- [user-manual.md](/Users/vv/Code/Yamibo/docs/user-manual.md)  
  面向日常使用者的操作手册
- [deployment-guide.md](/Users/vv/Code/Yamibo/docs/deployment-guide.md)  
  部署、备份、Daemon、MCP 集成
- [api-reference.md](/Users/vv/Code/Yamibo/docs/api-reference.md)  
  CLI / MCP / Web API 参考
- [development-guide.md](/Users/vv/Code/Yamibo/docs/development-guide.md)  
  面向开发者的代码与模块说明

## MCP / Agent 指南

- [agent-interface.md](/Users/vv/Code/Yamibo/docs/agent-interface.md)
- [mcp-guides/agent-workflows.md](/Users/vv/Code/Yamibo/docs/mcp-guides/agent-workflows.md)
- [mcp-guides/archive-model.md](/Users/vv/Code/Yamibo/docs/mcp-guides/archive-model.md)
- [mcp-guides/error-codes.md](/Users/vv/Code/Yamibo/docs/mcp-guides/error-codes.md)

说明：

- `docs/agent-evaluation.md` 与 `docs/mcp-guides/agent-evaluation.md` 存在主题重叠
- 当前更建议优先看 `docs/mcp-guides/` 下的版本

## 架构 / 设计 / 数据库

- [architecture-for-agents.md](/Users/vv/Code/Yamibo/docs/architecture-for-agents.md)
- [database-design.md](/Users/vv/Code/Yamibo/docs/database-design.md)
- [product-requirements.md](/Users/vv/Code/Yamibo/docs/product-requirements.md)
- [testing-strategy.md](/Users/vv/Code/Yamibo/docs/testing-strategy.md)
- [webui-design.md](/Users/vv/Code/Yamibo/docs/webui-design.md)
- [discussion-trend-v1.md](/Users/vv/Code/Yamibo/docs/discussion-trend-v1.md)

## 历史 / 阶段性报告

这些文档保留是有价值的，但它们不是当前操作真相来源：

- [history/rag-phase1-4-summary.md](/Users/vv/Code/Yamibo/docs/history/rag-phase1-4-summary.md)
- [history/rag-phase4-implementation-report.md](/Users/vv/Code/Yamibo/docs/history/rag-phase4-implementation-report.md)
- [history/rag-sqlite-vec-design.md](/Users/vv/Code/Yamibo/docs/history/rag-sqlite-vec-design.md)
- [history/fastapi-migration-plan.md](/Users/vv/Code/Yamibo/docs/history/fastapi-migration-plan.md)

当前判断：

- `docs/README.md`、`user-manual.md`、`deployment-guide.md`、`api-reference.md`、`development-guide.md` 可以视为“当前文档”
- 以 `rag-phase*`、`sqlite-vec`、迁移 plan 命名的文件，应视为“历史 / 设计过程文档”
- 当前版本统一为 `0.13.0`，如文档中出现更早版本号，应同步更新

## 当前接口注意点

- 单帖归档的主命令是 `create-thread-archive-job`；`create-sync-thread-job` 仅作为 legacy alias 保留
- 批量归档当前使用 `create-sync-thread-batch-jobs`
- 历史 file-only archive 的批量重同步 / PG 回填流程已落文档
- `cleanup-orphan-thread-dirs` 对“磁盘有归档、DB 无 thread row”的旧归档并不安全，使用前必须先确认缺失 tid 已处理
