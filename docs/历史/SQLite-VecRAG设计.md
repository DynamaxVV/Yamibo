# SQLite-Vec RAG 设计文档

> 历史说明：这是 SQLite-first 时期的 RAG 设计稿。当前正式路径已经是 PostgreSQL + `pgvector`；本文保留用于理解演化过程，不代表当前推荐实现。

> 版本：0.1 | 更新日期：2026-06-23 | 状态：设计草案

## 1. 背景

Yamibo Archive 当前是面向 yamibo.com 论坛的本地归档系统，已经具备结构化归档、楼层解析、标题解析、系列聚合、内容块模型、SQLite FTS5 搜索和 MCP 只读资源。现有搜索主要面向“帖子发现”，不适合直接回答 LLM 的内容查询问题。

本设计目标是在不查询图片内容的前提下，为本地归档数据增加面向 LLM 查询的 RAG 能力。底层向量检索选型固定为 `sqlite-vec`，embedding 模型倾向使用 OpenAI `text-embedding-3-small`，维度在 512 与 256 中选择。

## 2. 目标

- 支持对本地归档文本内容进行语义检索。
- 检索结果必须可追溯到帖子、楼层和资源 URI。
- 保留论坛数据的结构化优势，支持按分区、内容类型、系列、帖子、章节、作者等过滤。
- 不查询、OCR、描述或向量化图片内容。
- 与现有 job-based 架构一致：归档、更新、重建索引等耗时操作由 daemon 执行。
- 保持 SQLite-first，本地部署无需额外向量数据库服务。

## 3. 非目标

- 不做图片语义搜索、图片 OCR、图片 caption embedding。
- 不替代现有 `search_forum_threads` 的远端优先搜索。
- 不把 RAG 索引作为业务事实源；业务事实源仍是 `threads`、`floors`、`title_parse`、`series`、`content_blocks` 和物化归档文件。
- 不在第一阶段实现复杂 Agent 自动问答链；先实现稳定、可测试的检索接口。
- 不引入独立向量数据库、队列系统或外部搜索服务。

## 4. 当前数据特征

### 4.1 论坛归档形态

- `threads` 是帖子级主表，包含 `tid`、标题、发布者、发布时间、归档状态、分区、内容类型等。
- `floors` 是楼层表，`pid` 和 `floor_no` 是天然引用单元。
- `title_parse` 提供汉化组、作者、核心标题、系列 key、章节名、章节序号等结构化字段。
- `series` 管理同一作品或主题下的多个帖子。
- `content_blocks` 表示有序内容块，包含 text/image/attachment/quote/link 等类型。
- `assets` 管理图片与附件，但本设计不对图片内容做检索。

### 4.2 内容类型差异

| 内容类型 | 数据特征 | RAG 策略 |
|----------|----------|----------|
| `comic` | 图片为主，正文通常较短 | 主要索引标题、标题解析、楼主说明、非空回复文本 |
| `novel` | 长文本为主，楼层可能很长 | 按楼层和段落切 chunk，优先做语义检索 |
| `discussion` | 回复多、短文本多、引用多 | 楼层级 chunk + 元数据过滤，短回复可只进入 FTS |
| `mixed` | 文本和图片混合 | 文本部分进入索引，图片仅保留资产引用 |

## 5. 选型

### 5.1 向量库：sqlite-vec

选择 `sqlite-vec` 作为向量检索底层：

- 与当前 SQLite 架构一致，不需要额外服务。
- 适合本地、中小规模归档。
- 通过虚拟表存储和查询向量，便于与业务表 join。
- 运维成本低，备份策略仍围绕 SQLite 数据库。

风险：

- `sqlite-vec` 当前仍处于快速演进阶段，接口和能力可能变化。
- Python wheel、平台扩展加载方式需要在安装和测试中验证。

规避方式：

- 新增独立 repository 封装 `sqlite-vec` SQL。
- application 层只依赖领域方法，不直接写 `vec0` 查询。
- migration 和初始化阶段检测扩展可用性，错误信息必须明确。

### 5.2 Embedding 模型：text-embedding-3-small

默认模型：

```text
text-embedding-3-small
```

OpenAI 官方文档说明该模型默认输出 1536 维向量，并支持通过 `dimensions` 参数降低维度。`text-embedding-3-small` 的最大输入长度为 8192 tokens。

维度选择：

| 维度 | 优点 | 缺点 | 建议 |
|------|------|------|------|
| 512 | 质量更稳，仍显著小于 1536；适合中文/日文混合标题与长文本 | 存储和计算约为 256 的 2 倍 | 默认值 |
| 256 | 存储更小、查询更快、成本后处理更低 | 语义召回质量风险更高，需要评测确认 | 作为可配置实验选项 |

设计结论：第一版默认 `dimensions=512`。保留配置项支持切换到 256，但切换维度必须触发重建向量表和索引。

## 6. 总体架构

采用混合检索：

```text
本地归档数据
  -> chunk 构建
  -> rag_chunks
  -> rag_chunks_fts     关键词检索
  -> rag_chunk_vec      sqlite-vec 语义检索
  -> 混合召回与重排
  -> 带 tid/pid/floor_no/source_uri 的证据片段
  -> LLM 基于证据回答
```

检索默认策略是 `hybrid`：

1. 解析用户 query，提取可能的结构化过滤条件。
2. FTS5 召回关键词匹配结果。
3. sqlite-vec 召回语义相近结果。
4. 合并、去重、归一化分数。
5. 使用元数据加权重排。
6. 返回证据片段；需要时扩展相邻楼层上下文。

## 7. 数据模型

### 7.1 RAG chunk 主表

```sql
CREATE TABLE IF NOT EXISTS rag_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chunk_id TEXT NOT NULL UNIQUE,
  tid INTEGER NOT NULL,
  pid INTEGER,
  floor_no INTEGER,
  chunk_type TEXT NOT NULL,
  forum_id INTEGER,
  content_kind TEXT,
  series_id INTEGER,
  series_key TEXT,
  chapter_index REAL,
  publisher TEXT,
  pub_time TEXT,
  title TEXT,
  metadata_text TEXT,
  text TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  embedding_model TEXT,
  embedding_dimensions INTEGER,
  embedding_status TEXT NOT NULL DEFAULT 'pending',
  indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_tid ON rag_chunks(tid);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_pid ON rag_chunks(pid);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_series ON rag_chunks(series_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_forum_kind ON rag_chunks(forum_id, content_kind);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding_status ON rag_chunks(embedding_status);
```

`chunk_type` 取值：

- `thread_title`
- `thread_summary`
- `floor`
- `content_block`
- `quote`

### 7.2 FTS 表

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
  chunk_id UNINDEXED,
  title,
  metadata_text,
  body
);
```

说明：

- `title` 存标题、核心标题、章节标题。
- `metadata_text` 存作者、汉化组、series key、标签等。
- `body` 存 chunk 正文。
- 如 SQLite 构建支持 trigram tokenizer，可评估 `tokenize='trigram'`；否则使用默认 tokenizer，并保留 LIKE fallback。

### 7.3 sqlite-vec 表

默认 512 维：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunk_vec USING vec0(
  embedding float[512]
);
```

如果配置为 256 维，则表定义为：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunk_vec USING vec0(
  embedding float[256]
);
```

约束：

- `rag_chunk_vec.rowid` 必须等于 `rag_chunks.id`。
- 维度变化时不能原地兼容旧表，必须重建 `rag_chunk_vec` 并重新生成 embedding。

### 7.4 索引元数据

```sql
CREATE TABLE IF NOT EXISTS rag_index_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

建议键：

- `embedding_model`
- `embedding_dimensions`
- `embedding_provider`
- `embedding_created_at`
- `chunker_version`
- `sqlite_vec_version`

## 8. Chunk 构建规则

### 8.1 通用规则

- 空文本不生成 chunk。
- 每个 chunk 必须包含可追溯定位：`tid`，可选 `pid`、`floor_no`。
- 每个 chunk 必须包含 `source_uri`，优先使用现有 MCP resource URI。
- `text_hash` 由规范化后的文本和关键元数据生成，用于判断是否需要重建 embedding。
- chunk 文本中不包含图片 URL 列表，避免让 LLM 误以为可查询图片内容。

### 8.2 comic

- 生成 `thread_title` chunk。
- 楼主楼层有文字时生成 `floor` chunk。
- 其他楼层仅当正文长度达到阈值时生成 chunk。
- 图片块不生成 embedding。

### 8.3 novel

- 标题和 metadata 生成 `thread_title` chunk。
- 楼层正文按段落切分。
- 单 chunk 建议控制在 500-900 中文字符。
- 长段落按标点边界切分，必要时硬切。
- 每个 chunk 保留原始 `pid` 和 `floor_no`。

### 8.4 discussion

- 楼层正文达到最小长度时生成 `floor` chunk。
- 短回复可以只进入 FTS，不一定生成 embedding；第一版可统一生成 embedding，后续根据规模优化。
- `quote_text` 可作为单独 `quote` chunk 或拼入 metadata，第一版建议拼入同楼层 chunk。

## 9. 检索策略

### 9.1 keyword

只使用 `rag_chunks_fts` 和结构化过滤。

适合：

- 明确标题、作者、汉化组、章节号。
- 调试和无 embedding 配置时 fallback。

### 9.2 vector

只使用 query embedding 和 `rag_chunk_vec`。

适合：

- 语义相近但关键词不确定的问题。
- 轻小说内容检索。

### 9.3 hybrid

默认模式。

候选来源：

- FTS top N
- vector top N
- 可选 metadata exact match top N

合并后重排：

```text
score =
  vector_score * 0.45
  + keyword_score * 0.35
  + metadata_score * 0.20
```

第一版不必过度调参，但必须保留分数分量，便于后续评测。

metadata 加分：

- `tid` 精确匹配
- `series_id` 或 `series_key` 匹配
- `forum_id` / `content_kind` 匹配
- 标题字段命中
- 楼主楼层
- 章节号接近
- `archive_status` 为 `complete`

## 10. MCP 与应用层接口

### 10.1 新增工具：search_archived_content

本地只读，不抓远端。

参数建议：

```json
{
  "query": "string",
  "mode": "hybrid | keyword | vector",
  "top_k": 10,
  "forum_id": null,
  "content_kind": null,
  "tid": null,
  "series_id": null,
  "floor_start": null,
  "floor_end": null
}
```

返回建议：

```json
{
  "query": "...",
  "mode": "hybrid",
  "count": 3,
  "items": [
    {
      "chunk_id": "...",
      "tid": 572313,
      "pid": 12345,
      "floor_no": 1,
      "display_title": "...",
      "publisher": "...",
      "pub_time": "...",
      "content_kind": "novel",
      "snippet": "...",
      "score": 0.82,
      "score_parts": {
        "vector": 0.77,
        "keyword": 0.61,
        "metadata": 1.0
      },
      "source_uri": "yamibo://threads/572313/posts"
    }
  ]
}
```

### 10.2 新增工具：create_rag_index_job

创建后台索引任务，不同步执行。

参数：

```json
{
  "tid": null,
  "force": false,
  "embedding_dimensions": 512
}
```

语义：

- `tid` 为空时重建全部归档索引。
- `force=false` 时只处理缺失或 hash 变化的 chunk。
- `force=true` 时删除并重建指定范围索引。

### 10.3 可选工具：answer_archived_query

第二阶段再实现。该工具内部调用 `search_archived_content`，再调用 LLM 基于证据回答。

第一阶段可以先不实现，避免把检索正确性和生成质量混在一起。

## 11. 配置

新增配置项：

```json
{
  "rag": {
    "enabled": true,
    "embedding_provider": "openai",
    "embedding_model": "text-embedding-3-small",
    "embedding_dimensions": 512,
    "chunker_version": "rag-chunker-v1",
    "min_chunk_chars": 20,
    "max_chunk_chars": 900,
    "hybrid_fts_candidates": 50,
    "hybrid_vector_candidates": 50
  }
}
```

环境变量建议：

- `YAMIBO_RAG_ENABLED`
- `YAMIBO_RAG_EMBEDDING_MODEL`
- `YAMIBO_RAG_EMBEDDING_DIMENSIONS`
- `YAMIBO_RAG_MIN_CHUNK_CHARS`
- `YAMIBO_RAG_MAX_CHUNK_CHARS`

OpenAI API 配置复用现有 `llm` 配置，避免新增第二套 API key。

## 12. Job 流程

新增 job 类型：

```text
rag_index
```

阶段：

| 阶段 | 说明 |
|------|------|
| `collect` | 找到需要索引的帖子 |
| `chunk` | 从 SQLite 业务表构建 chunk |
| `fts` | 写入 `rag_chunks` 和 `rag_chunks_fts` |
| `embed` | 调用 embedding API |
| `vec` | 写入 `rag_chunk_vec` |
| `verify` | 校验 chunk、FTS、向量数量一致 |

归档集成：

- `sync_thread` 成功或 `partial` 后，可以创建或内联执行单帖索引更新。
- 第一版建议显式创建 `rag_index` 子任务，减少 sync handler 复杂度。
- 图片下载失败不影响文本索引。

## 13. Repository 与模块边界

建议新增：

```text
src/yamibo_mcp/rag/
  chunker.py
  embeddings.py
  search.py
  scoring.py

src/yamibo_mcp/db/repositories/
  rag_chunks.py
  rag_vectors.py

src/yamibo_mcp/application/
  rag_commands.py
  rag_queries.py

src/yamibo_mcp/daemon/handlers/
  rag_index.py
```

边界要求：

- `application/rag_queries.py` 本地只读，不能调用远端论坛 client。
- `rag_vectors.py` 是唯一直接操作 `sqlite-vec` SQL 的位置。
- `rag/embeddings.py` 是唯一调用 embedding API 的位置。
- `rag/chunker.py` 不调用数据库，只接收结构化输入并输出 chunk。

## 14. 测试策略

### 14.1 单元测试

- chunker：
  - comic 不索引图片内容。
  - novel 长楼层切 chunk。
  - discussion 短回复处理。
  - chunk_id 稳定。
  - text_hash 内容变化后变化。
- repositories：
  - `rag_chunks` upsert/delete/list。
  - FTS 写入与搜索。
  - sqlite-vec 扩展不可用时错误明确。
- search：
  - keyword 模式。
  - vector 模式使用 fake embedding。
  - hybrid 去重与重排。
- application：
  - 本地缺索引时返回可操作错误。
  - 不触发远端抓取。

### 14.2 集成测试

- 使用 fixtures 中的真实论坛数据构建索引。
- 对轻小说、漫画、讨论三类数据分别验证检索结果有 `tid/pid/floor_no/source_uri`。
- 使用 fake embedding provider 避免测试依赖网络。

### 14.3 评测建议

维护一组查询样例：

- 标题精确查询。
- 作者/汉化组查询。
- 系列章节查询。
- 轻小说语义问题。
- 讨论区关键词问题。
- 漫画区图片内容问题，预期应明确无法基于图片回答。

## 15. 风险与决策

| 风险 | 影响 | 决策 |
|------|------|------|
| 256 维召回质量不足 | 语义检索漏召回 | 默认 512，256 作为实验配置 |
| sqlite-vec 扩展加载失败 | RAG 不可用 | 普通归档功能不受影响，错误提示安装/环境问题 |
| embedding API 失败 | 索引不完整 | chunk/FTS 仍可用，向量状态标记 failed |
| 中文 FTS 分词效果不稳定 | 关键词召回不佳 | 保留 LIKE fallback，评估 trigram tokenizer |
| 漫画图片不可查 | 漫画内容问答能力有限 | 明确只基于文本元数据和楼层文字回答 |

## 16. 推荐落地顺序

1. 增加 RAG 配置和 migration。
2. 实现 `rag_chunks` repository 与 chunker。
3. 实现 keyword 检索，先不接 embedding。
4. 接入 `sqlite-vec` 扩展加载和 vector repository。
5. 实现 embedding provider，默认 `text-embedding-3-small` + 512 维。
6. 实现 `rag_index` job。
7. 实现 vector 检索。
8. 实现 hybrid 检索和重排。
9. 暴露 MCP tool 和 resource hints。
10. 补齐 fixtures 驱动测试和 README/API 文档更新。

## 17. 参考

- OpenAI Embeddings Guide: `text-embedding-3-small` 默认 1536 维，可用 `dimensions` 参数降低维度。
- sqlite-vec 官方文档：`vec0` 虚拟表用于 SQLite 内向量存储与 KNN 查询。
