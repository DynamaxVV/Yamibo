# PostgreSQL 迁移运行手册

这是 Phase 6 / Phase 7 的正式执行手册，记录 SQLite -> PostgreSQL 切换、影子验证、切流和回滚路径。

当前阶段状态见：[docs/phase6-7-status-summary.md](phase6-7-status-summary.md)。

## 预检

1. 确认 PostgreSQL 15+ 已启动。
2. 确认 `pgvector` 和 `pg_trgm` 已安装可用。
3. 先备份 SQLite 数据库。
4. 影子验证期间冻结 daemon 的写入流量。

## ETL 迁移

```bash
uv run python scripts/migrate_sqlite_to_postgres.py \
  --source-db data/forum.db \
  --target-db-url "$YAMIBO_DB_URL" \
  --schema public
```

脚本会：

- 将源表复制到 PostgreSQL；
- 保留主键；
- 如果源库存在向量数据，则恢复 `rag_chunks.embedding`；
- 重建搜索索引；
- 执行 `ANALYZE`；
- 输出包含复制行数的 JSON 报告。

## 影子验证

- 影子窗口内保持 PostgreSQL 只读。
- 对齐切流最关键的只读接口：
  - 控制台首页
  - 搜索
  - RAG 概览
- 将不一致记录到 `docs/pg-shadow-validation-report.md`。
- 使用以下命令生成 JSON diff 报告：

```bash
uv run python scripts/validate_shadow_readonly.py \
  --sqlite-db ... \
  --postgres-db-url ... \
  --baseline tests/fixtures/search_regression_baseline.json
```

- 在差异列表为空或已明确接受之前，不要进入切流。

## 切流

1. 停止 daemon、web 和 MCP server。
2. 做最后一次 SQLite 备份。
3. 运行 ETL 脚本。
4. 运行回归基线检查。
5. 将配置切换为 `db_backend=postgres` 和 `db_url=...`。
6. 启动服务。
7. 进入 24 小时只读观察期。

## 回滚

- 在 24 小时观察期内，回滚路径为：
  1. 停止服务；
  2. 恢复 SQLite 备份；
  3. 将配置切回 SQLite；
  4. 重新启动服务。
- 如果 PostgreSQL 已经接收新写入，则回滚需要反向 ETL，不在本阶段范围内。
