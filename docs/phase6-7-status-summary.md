# Phase 6 / 7 状态摘要

日期：2026-06-27

## 已完成

- SQLite -> PostgreSQL ETL：`scripts/migrate_sqlite_to_postgres.py`
- 只读影子对比：`scripts/validate_shadow_readonly.py`
- 切流 / 回滚手册：`docs/postgres-migration-runbook.md`
- 影子验证报告：`docs/pg-shadow-validation-report.md`
- PostgreSQL 回归基线：`tests/fixtures/search_regression_baseline.json`
- PostgreSQL 启动流程已支持隔离的 `db_schema` 测试 schema

## 已在本工作区验证

- `uv run pytest -q`
- 最近一次完整测试结果：`784 passed, 6 skipped in 13.80s`
- ETL 烟雾测试在真实 PostgreSQL 测试库上通过
- 影子验证烟雾测试在 SQLite + PostgreSQL 上通过
- PostgreSQL 回归测试通过
- 对 `data/forum.db` 的真实 SQLite -> PostgreSQL ETL 已完成，耗时 118.938 秒，schema 为 `phase6_real2`
- 20 个帖子搜索查询的直接 source-vs-target 影子对比达到 90% top-k 完全匹配；向量查询完全匹配
- 1 条孤儿 `job_events` 记录需要在 ETL 中补一条 `jobs` 修复记录，才能满足 PostgreSQL 外键约束

## 仍需外部环境才能完成的事项

- 10 GB 级别数据量在目标机器上的完整 ETL
- 7 天或 1000 次请求的影子验证窗口
- 实际切流
- 24 小时只读观察期

## 当前切流姿态

- 仓库里已经有执行切流所需的脚本和文档。
- 当前剩余缺口是外部环境执行和对 1 个源数据异常修复的接受，不是缺少代码。
- 观察期内的回滚路径已记录为恢复 SQLite 备份。

## 验收覆盖

- AC-5：ETL 脚本已存在，并在本地 PostgreSQL fixture 上完成测试。
- AC-4：回归基线文件已存在，PG 回归测试通过。
- Phase 6 影子验证：脚本已存在并完成烟雾测试。
- Phase 7 回滚：已在运行手册中说明，未额外引入反向 ETL。
