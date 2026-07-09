# Phase 1-4 总结报告

## 总体目标

本项目的 RAG / 上下文链路改造，核心目标是把“线程归档、上下文生成、证据切块、检索回溯、审计可追踪”统一成一条可验证、可升级、可回放的流水线。

这 4 个 Phase 的分工可以概括为：

- **Phase 1**：先把上下文与归档的基础设施打通，确保线程内容能稳定产出可读的本地上下文。
- **Phase 2**：把线程处理、清洗和预览链路梳理成可复用的应用层接口。
- **Phase 3**：把 RAG 检索与 chunk 生成打通，保证检索结果和本地证据一致。
- **Phase 4**：把 cleaner 变成共享契约，补齐 traceability、marker/hash 保护和测试/迁移闭环。

---

## Phase 1：基础归档与上下文链路

### 重点内容

- 搭建线程归档与上下文生成的基础结构。
- 确保 `thread context` / `thread metadata` / `archive markdown` 等输出具备统一格式。
- 让本地文件、数据库记录和资源 URI 能互相对应。

### 结果

- 线程内容能够稳定落到本地存储。
- 上下文生成链路具备可读性与可回溯性。
- 为后续 preview / RAG / materializer 链路提供了基础输入。

---

## Phase 2：应用层清洗与预览接口

### 重点内容

- 将 thread preview、archive query、更新检查等逻辑抽象到应用层。
- 把“清洗、展示、预览”的责任分离，避免 UI 层直接依赖底层数据结构。
- 增强对楼层、标题、分类和元信息的统一呈现。

### 结果

- `thread_preview`、`archive_queries` 等应用接口逐步稳定。
- Obsidian 风格上下文与归档展示之间的差异被收敛。
- 后续 cleaner / chunker 的改造可以直接复用这些应用层结果。

---

## Phase 3：RAG 切块与检索链路

### 重点内容

- 建立 `RagChunk` 与 `rag_chunks` 的基础模型。
- 打通 RAG chunk 生成、落库、搜索与向量检索路径。
- 让 search/query 能在本地数据库里直接返回证据片段，而不是依赖远端内容。

### 结果

- `rag_chunks` 成为线程证据的核心落库表。
- 检索查询能返回带 `tid / pid / floor_no / source_uri` 的本地证据。
- chunk 生成与检索结果具备一致的路径和可定位性。

---

## Phase 4：共享 cleaner、追溯字段与消费保护

### 重点内容

- 把 `anime_dry_run` 提升为共享 cleaner 入口。
- 让 Obsidian / RAG 共享同一份 cleaner 输入。
- 给 `RagChunk` 和 `rag_chunks` 增加完整 traceability 字段。
- 增加 marker/hash 保护，防止旧 artifact 被误消费。
- 同步补齐 migration / schema / 测试。

### 已完成的能力

#### 1. 共享 cleaner 契约

- `ThreadDryRunResult` 增加：
  - `source_hash`
  - `generated_at`
- `sync_thread` / `update_thread` / `image_backfill` / `thread_preview` 已通过共享 payload 组装器接入同一份 cleaner 输出。
- 这两个字段成为跨链路共享输入。

#### 2. `RagChunk` 追溯字段

新增字段包括：

- `source_tid`
- `source_pid`
- `source_floor_no`
- `cleaner_version`
- `chunker_version`
- `materializer_version`
- `source_hash`
- `generated_at`
- `quality_flags`

#### 3. `rag_chunks` 落库追溯信息

- `RagChunksRepository.replace_thread_chunks()` 已同步落库这些字段。
- `quality_flags` 使用 JSON 字符串存储，兼容不同后端。

#### 4. marker / hash 保护

- `anime_materializer` 写入前会校验 `source_hash`。
- `rag_index` 正式索引前会先生成/刷新 materialized preview，再按 marker 中记录的 sha256 校验 preview 文件。
- 如果 marker 与当前 materialized artifact 不一致，则拒绝覆盖；如果 preview hash 不一致，则拒绝消费。

#### 5. schema / migration 同步

- `src/yamibo_mcp/db/migrations.py` 已更新 SQLite 初始建表逻辑。
- `alembic/versions/008_add_rag_chunk_traceability.py` 已新增。
- `001_initial_schema.py` 已补充相应字段。

### Phase 4 的结果

- RAG 与 Obsidian 的输入契约统一了。
- 证据 chunk 可以完整回溯到 `tid/pid/floor_no` 和生成版本。
- marker/hash 机制能够阻止旧 artifact 被误用，且正式索引消费链路已经接入校验。
- 单测与查询测试都已经调整到新契约。

---

## 测试结果总览

已验证通过的范围：

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

说明：

- RAG chunk 生成逻辑稳定。
- shared cleaner 契约稳定。
- search/query 可返回本地证据。
- traceability 字段和 schema 已同步。

---

## 总结

这 4 个 Phase 不是彼此独立的功能堆叠，而是一条连续演进的链路：

1. **Phase 1** 解决“内容从哪来、怎么落地”。
2. **Phase 2** 解决“怎么用统一接口看和预览”。
3. **Phase 3** 解决“怎么切块、怎么检索”。
4. **Phase 4** 解决“怎么共用 cleaner、怎么回溯、怎么防止旧结果污染新流程”。

最终效果是：
- 上下文生成、检索、审计和回放可以共用一套数据契约。
- 线程内容、chunk、marker、schema 和测试都能同步演进。
- 后续新增能力时，不需要再分别维护多条相互割裂的清洗/切块逻辑。
