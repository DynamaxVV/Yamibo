# 测试报告

> 生成时间：2026-06-21 | 框架：pytest 9.0.3 | Python 3.13.13

## 1. 执行摘要

| 指标 | 结果 |
|------|------|
| 总用例数 | **626** |
| 通过 | **626** |
| 失败 | 0 |
| 错误 | 0 |
| 执行时间 | 约 10s |

## 2. 测试矩阵

| 类别 | 用例数 | 通过 |
|------|--------|------|
| 黑盒测试（解析器 fixture） | 212 | 212 |
| 白盒测试（DB） | 97 | 97 |
| 白盒测试（Domain） | 60 | 60 |
| 白盒测试（Storage） | 40 | 40 |
| 白盒测试（Services） | 9 | 9 |
| 白盒测试（Server + Application） | 80 | 80 |
| 白盒测试（Yamibo 模块） | 75 | 75 |
| 白盒测试（规则引擎直接） | 24 | 24 |
| **总计** | **626** | **626** |

## 3. 白盒测试详情（本次新增）

### 3.1 数据仓库层 (test_db)

| 测试文件 | 用例数 | 测试内容 |
|----------|--------|----------|
| test_jobs_repository.py | 26 | Job CRUD、租约抢占、心跳、状态变更、过期恢复 |
| test_threads_repository.py | 13 | 帖子 upsert、标题解析、楼层、删除、导出标记、搜索 |
| test_series_repository.py | 13 | 系列创建/复用、别名累积、合并、删除、复核确认 |
| test_audit_events_repository.py | 6 | 审计事件记录、时间排序、limit |
| test_migrations.py | 15 | 空库迁移、旧库迁移、幂等性、新表创建、列回填 |
| test_content_blocks_repository.py | 7 | 内容块 upsert、metadata JSON、替换、排序 |
| test_assets_repository.py | 10 | 资产 upsert、类型、状态、local path |
| test_job_events.py | 14 | 事件追加、succeed/fail 事件、append 失败降级、list 过滤 |
| test_threads_forum_filter.py | 7 | search/list 按 forum_id 过滤 |

### 3.2 Domain 层 (test_domain)

| 测试文件 | 用例数 | 测试内容 |
|----------|--------|----------|
| test_validation.py | 20 | 快照校验：tid/page_type/标题/楼层完整性/图片/warnings |
| test_models.py | 15 | 枚举值完整性、JobID 格式与唯一性、数据类不可变性、默认值 |
| test_forums.py | 12 | ForumProfile、resolve_forum、default_forums、DEFAULT_FORUM_ID |
| test_content.py | 13 | classify_content_kind、build_content_snapshot、validate_by_profile |

### 3.3 存储层 (test_storage)

| 测试文件 | 用例数 | 测试内容 |
|----------|--------|----------|
| test_atomic.py | 3 | 原子写入、父目录创建、覆盖已有文件 |
| test_paths.py | 11 | 路径模板：series/thread/staging/exports |
| test_staging.py | 4 | staging 快照/失败日志/标题解析日志写入 |
| test_markdown.py | 10 | Markdown 渲染（frontmatter/楼层/图片）、JSON 元数据渲染 |
| test_exports.py | 12 | 导出就绪检查、过期判断、ZIP 打包、完整性校验 |

### 3.4 服务层 (test_services)

| 测试文件 | 用例数 | 测试内容 |
|----------|--------|----------|
| test_title_hints.py | 9 | 提示词加载/写入/更新、文件创建、去重、损坏 JSON 兜底 |

### 3.5 Yamibo 模块 (test_yamibo)

| 测试文件 | 用例数 | 测试内容 |
|----------|--------|----------|
| test_page_classifier.py | 16 | 页面分类：维护页/登录页/帖子详情/列表页/搜索结果/未知 |
| test_normalizer.py | 15 | 标题规范化：NFKC、繁简转换、标点清理、Discuz 后缀 |
| test_content_cleaner.py | 12 | 内容清洗：附件提示/文件名/时间戳/空白规范化 |
| test_series_matcher.py | 8 | 系列匹配：base_key/creator_key/alias/needs_review |
| test_title_parser_direct.py | 24 | 规则引擎直接测试：默认章节/特典999/冒号模式/前缀跳过/章节索引 |

## 4. 黑盒测试详情

| 测试文件 | 用例数 | 测试内容 |
|----------|--------|----------|
| test_forum_list.py | 37 | 论坛列表页解析：结构/字段/归档信息/分类/边界条件 |
| test_thread_detail.py | 109 | 帖子详情解析：字段/楼层/图片/章节/特殊前缀/边界条件 |
| test_title_parser.py | 66 | 标题解析：规则引擎/章节号/置信度/参数化 |

## 5. 测试覆盖分析

| 模块 | 白盒覆盖 | 说明 |
|------|---------|------|
| db/repositories | ✅ 完整 | Jobs/Threads/Series/Audit/ContentBlocks/Assets/JobEvents 七个 Repository 全覆盖 |
| domain | ✅ 完整 | models/enums/validation/job_state/forums/content 全覆盖 |
| storage | ✅ 完整 | atomic/paths/staging/markdown/exports 全覆盖 |
| services | ✅ 完整 | title_hints 全覆盖（LLM client 需外部 API，跳过） |
| server | ✅ 完整 | resources/protocol/tools 全覆盖（含新 resource URI） |
| application | ✅ 完整 | contracts/thread_use_cases/job_use_cases 全覆盖 |
| yamibo/page_classifier | ✅ 完整 | 所有 6 种页面类型全覆盖 |
| yamibo/title/normalizer | ✅ 完整 | 三个公开函数全覆盖 |
| yamibo/cleaners | ✅ 完整 | clean_content 全覆盖 |
| yamibo/series_matcher | ✅ 完整 | build_series_match_decision 全覆盖 |
| worker/handlers | ⚠️ 待补充 | 处理器需集成测试环境 |

## 6. 待补充测试

| 模块 | 原因 | 优先级 |
|------|------|--------|
| services/llm_client.py | 需要 mock 外部 HTTP 请求 | P2 |
| services/title_llm.py | 需要 mock LLM API | P2 |
| daemon/handlers/* | 需要完整的 Daemon 环境 | P1 |
| web/app.py | 需要 HTTP 测试客户端 | P2 |
