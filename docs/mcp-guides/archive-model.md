# Archive Model

> 远端只读 / 本地只读 / 后台任务 三层边界。

## 三层模型

### 第一层：远端只读（Remote Read-Only）

读取论坛实时数据，不修改本地状态。

**CLI**：
```bash
uv run yamibo-archiver browse-forum-page --page 1
uv run yamibo-archiver search-threads --query "..."
uv run yamibo-archiver check-thread-updates --tid <tid>
```

**约束**：可读取本地归档数据库补充"是否已归档"提示。不写 thread/floor/content_blocks/assets。不下载图片。

### 第二层：本地只读（Local Read-Only）

读取已归档的本地数据，绝不抓远端。

**CLI**：
```bash
uv run yamibo-archiver search-archived-content --query "..." --mode hybrid
uv run yamibo-archiver read-resource "yamibo://threads/<tid>/summary"
uv run yamibo-archiver read-resource "yamibo://threads/<tid>/diagnostics"
uv run yamibo-archiver job-status <job_id>
```

**MCP**：调用 `read_archived_thread` 工具，`content` 视图支持 `has_more` / `next_cursor` 分页。任务状态通过 `read_job` / `read_job_events` 读取。

### 第三层：后台任务（Background Job）

创建任务 → daemon 消费 → 读取结果。

**CLI**：
```bash
uv run yamibo-archiver create-thread-archive-job --tid <tid>
uv run yamibo-archiver create-export-thread-job --tid <tid>
uv run yamibo-archiver create-rag-index-job --tid <tid>
```

**约束**：命令层只创建 job，执行由 daemon 负责。结果通过 `job-status` 或资源 URI 读取。

## 批量操作

批量归档前先用探测工具判断是否需要补跑：

```bash
uv run yamibo-archiver probe-archived-threads --tid <tid1> --tid <tid2>
```

探测只读本地归档状态，不创建 job，不抓远端。
