# PostgreSQL Schema 决策

> 记录日期：2026-06-27  
> 状态：Phase 0 baseline

## 作用范围

这份记录描述 PostgreSQL 切流前需要稳定下来的迁移决策。

## JSON 列审计

当前这些 JSON 编码字段在 Python 侧大多会按结构化数据读取，因此在 PostgreSQL 中应优先使用 `JSONB`；只有明确只是当作字符串存储的字段才保留为 `TEXT`。

| 表 | 列 | 决策 | 原因 |
|------|------|------|------|
| jobs | `payload_json` | `JSONB` | 在任务生命周期代码中会解析和合并 |
| jobs | `artifacts_json` | `JSONB` | Web/API 和 job event payload 都会消费 |
| job_events | `payload_json` | `JSONB` | 事件 payload 需要按结构化数据回读 |
| audit_events | `before_json` / `after_json` | `JSONB` | 审计 diff 会在 Python 中渲染和过滤 |
| series | `alias_keys_json` / `aliases_json` | `JSONB` | 系列元数据以列表方式合并 |
| threads | `validation_errors_json` / `missing_images_json` | `JSONB` | 校验状态按结构化列表消费 |
| content_blocks | `metadata_json` | `JSONB` | 块级元数据是结构化内容，后续可能扩展 |
| title_parse | `title_aliases_json` / `tags_json` / `warnings_json` | `JSONB` | 标题解析结果按结构化数据消费 |
| sync_runs | `warnings_json` / `errors_json` | `JSONB` | 运行诊断是结构化 payload |
| rag_chunks | `metadata_text` | 暂时保留 `TEXT` | 自由文本搜索元数据，目前还不够结构化 |
| rag_index_meta | `value` | `TEXT` | 透明 key/value 元数据 |

## Alembic 基线策略

- 先从当前 SQLite schema 形状生成第一版 PostgreSQL revision，然后让 Alembic 接管后续所有 PostgreSQL 迁移。
- 迁移过渡期内，SQLite 的 seed / migration 逻辑继续独立，不要强行让两个引擎共用同一条迁移历史。
- `schema_migrations` 继续作为 SQLite 侧版本表。
- PostgreSQL 使用 Alembic 的 `alembic_version` 表，并配一个与当前 schema 和索引一致的 baseline revision。
- `db_schema` 预留给隔离的 PostgreSQL 测试 schema 和后续集成工作。

## 切流说明

- Phase 0 不应在 PostgreSQL 分支里引入新的 schema 形状。
- 只存储、从不按结构化方式查询的列可以暂时保留为 `TEXT`，直到出现明确使用场景。
- 包括 `vector` 和搜索专用索引在内的完整 DDL，会在抽象层落地后再补齐。
