# Agent Evaluation Guide

> Agent 能力验收场景与评分维度。

完整评估标准见 [docs/agent-evaluation.md](../agent-evaluation.md)。

## 核心验收场景

### 1. 论坛浏览与搜索

```bash
uv run yamibo-archiver browse-forum-page --page 1
uv run yamibo-archiver search-threads --query "星灵感应"
```

验证点：正确解析论坛列表、搜索结果，识别帖子元数据。

### 2. 归档创建与状态跟踪

```bash
uv run yamibo-archiver create-thread-archive-job --tid <tid>
uv run yamibo-archiver job-status <job_id>
```

验证点：任务正确入库，daemon 消费执行，状态转换正确。

### 3. 本地归档读取

```bash
uv run yamibo-archiver read-resource "yamibo://threads/<tid>/summary"
```

验证点：返回结构化数据，包含楼层、图片、元数据。

### 4. RAG 检索

```bash
uv run yamibo-archiver search-archived-content --query "..." --mode hybrid
```

验证点：返回相关片段，含溯源信息。

### 5. 错误处理

验证点：无效参数返回清晰错误；网络故障时正确处理；反爬触发时自动暂停。

## 评分维度

| 维度 | 权重 | 说明 |
|---|---|---|
| 功能正确性 | 40% | 各场景输出符合预期 |
| 错误恢复 | 25% | 异常状态下行为正确，不创建 duplicate live jobs |
| 性能 | 15% | 响应时间、并发处理 |
| 可诊断性 | 20% | partial/interrupted 状态可追踪 |
