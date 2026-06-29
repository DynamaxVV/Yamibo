# PostgreSQL 影子验证报告

用于记录只读影子验证运行结果。

## 运行信息

- 日期：2026-06-27
- SQLite 源库：`/Users/vv/Code/Yamibo/data/forum.db`
- PostgreSQL 目标：`postgresql+psycopg://vv@127.0.0.1:55433/vv`，schema 为 `phase6_real2`
- 耗时：同一轮运行内完成 118.938 秒 ETL + 直接影子对比

## 结果

- 采样请求：20 个帖子搜索查询，18 个向量搜索查询
- 帖子 top-k 完全匹配率：90%
- 向量结果完全匹配率：100%
- 不一致项：2 个帖子搜索查询的排序 / 头部结果不同；1 个 `job_events` 孤儿记录修复导致的 jobs 数量差异
- 延迟回归：本次未测量
- 错误：无

## 备注

- 源 SQLite 数据库里存在 1 条孤儿 `job_events` 记录（`rag_index_5c39ed63250843a3`），没有对应的 `jobs` 父记录。ETL 会补一条 PostgreSQL `jobs` 记录，保证外键图完整。
- 当前影子对比采用的是直接 source-vs-target，不是 fixture-baseline-vs-target。
