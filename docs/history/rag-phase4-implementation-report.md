# Phase 4 改动实现报告

## 概述

本次改动围绕 RAG 证据链与 Obsidian 上下文链路的统一展开，重点完成了“共享 cleaner 输出、可追溯 chunk、marker/hash 保护、以及测试与 schema 同步”四个方面。

## 已实现内容

### 1. 共享 cleaner 输出契约

- `yamibo_mcp.rag.anime_dry_run` 作为共享清洗入口继续承载 thread / floor 的结构化清洗结果。
- `ThreadDryRunResult` 增加了：
  - `source_hash`
  - `generated_at`
- 新增 `yamibo_mcp.application.thread_context.build_obsidian_context_payload()`，把 `sync_thread` / `update_thread` / `image_backfill` / `thread_preview` 的 Obsidian 输出统一接到同一份 cleaner 结果上。
- 这两个字段作为 Obsidian renderer 与 RAG materializer 的共同输入契约，确保两条链路使用同一份 cleaner 结果。

### 2. `RagChunk` 追溯字段扩展

`yamibo_mcp.rag.chunker.RagChunk` 已扩展为可追溯结构，新增字段包括：

- `source_tid`
- `source_pid`
- `source_floor_no`
- `cleaner_version`
- `chunker_version`
- `materializer_version`
- `source_hash`
- `generated_at`
- `quality_flags`

同时，`build_rag_chunks()` 现在复用 `anime_dry_run` 的共享 cleaner 输出，用统一契约生成 RAG chunk。

### 3. RAG materializer 与正式索引的 marker / hash 保护

`yamibo_mcp.rag.anime_materializer` 已接入 source hash 保护逻辑：

- 生成 materialized artifact 前会校验 `source_hash`
- 如果已有 marker 的 `source_hash` 与当前 materialized 不一致，则拒绝覆盖，避免旧 artifact 被错误消费
- `_chunk_record()` 已输出更完整的追溯信息，便于后续审计与回溯
- `yamibo_mcp.daemon.handlers.rag_index` 现在会先生成/刷新 `rag_materialized.1.2.json` 与 `rag_chunks.preview.1.2.jsonl`，然后按 marker 记录的 sha256 校验 preview，再写入正式 `rag_chunks`
- 因此 marker/hash 保护不再只停留在 shadow artifact，而是已经进入正式索引消费路径

### 4. `rag_chunks` 落库追溯字段

`yamibo_mcp.db.repositories.rag_chunks.RagChunksRepository.replace_thread_chunks()` 已同步写入以下追溯字段：

- `source_tid`
- `source_pid`
- `source_floor_no`
- `cleaner_version`
- `chunker_version`
- `materializer_version`
- `source_hash`
- `generated_at`
- `quality_flags`

其中 `quality_flags` 采用 JSON 字符串形式存储，便于兼容 SQLite 与 PostgreSQL。

### 5. Schema / migration 同步

完成了两层 schema 变更：

- `src/yamibo_mcp/db/migrations.py`
  - 更新了 SQLite 初始化建表语句，增加 `rag_chunks` 追溯字段
- `alembic/versions/008_add_rag_chunk_traceability.py`
  - 新增 Alembic migration，用于数据库升级

同时，`001_initial_schema.py` 也已补充 `rag_chunks` 追溯字段定义，确保从初始建库到后续迁移都一致。

## 测试调整

### 1. `tests/unit/test_rag/test_chunker.py`

已按新契约重写，重点验证：

- 追溯字段存在
- chunk id 稳定
- `source_hash` / `generated_at` 透传
- 共享 cleaner 输出可正确落到 `RagChunk`

### 2. `tests/unit/test_application/test_rag_queries.py`

已补齐适配新 `RagChunk` 构造参数，覆盖：

- keyword search 可返回本地证据
- snippet 截断与命中居中
- vector search 使用 fake provider
- 查询过程不回退到远端抓取

### 3. 新增/补齐的回归用例

- `tests/unit/test_application/test_thread_context.py`
  - 校验 Obsidian payload 直接消费 shared cleaner 输出
- `tests/unit/test_application/test_thread_preview.py`
  - 校验 preview 输出带 YAML frontmatter，且 board/reply_count/source_hash 来自新链路
- `tests/unit/test_daemon/test_rag_index_debug.py`
  - 校验正式 `rag_index` 读取 materialized preview
  - 校验 preview hash mismatch 会被拒绝
- `tests/unit/test_db/test_threads_repository.py`
  - 旧 `RagChunk(...)` 构造已迁移到新 traceability 契约

## 验证结果

已执行并通过：

- `tests/unit/test_application/test_thread_context.py`
- `tests/unit/test_application/test_thread_preview.py`
- `tests/unit/test_daemon/test_rag_index_debug.py`
- `tests/unit/test_db/test_threads_repository.py`
- `tests/unit/test_daemon/test_sync_thread_handler.py`
- `tests/unit/test_daemon/test_update_thread_handler.py`
- `tests/unit/test_daemon/test_image_backfill.py`
- `tests/unit/test_rag/*`
- `tests/unit/test_application/test_rag_queries.py`
- `tests/unit/test_server/test_forum_id_tools.py`

## 主要收益

- RAG 与 Obsidian 的输入来源统一，且真实归档链路已经接入
- 每个 chunk 都可回溯到 `tid/pid/floor_no` 与生成版本
- marker/hash 能阻止旧 artifact 被误消费，且正式索引消费会做 hash 校验
- SQLite / Alembic / 测试 schema 已同步，避免了“代码已改但测试库缺列”的问题

## 后续建议

- 若后续继续演进 cleaner 逻辑，优先保持 `source_hash` / `generated_at` 作为跨链路契约稳定存在
- 若新增 chunk 质量标识，建议继续沿用 `quality_flags`，避免扩散新的枚举字段
- 后续如要将追溯信息展示到 UI，可直接复用 `rag_chunks` 中的字段，无需重新计算
