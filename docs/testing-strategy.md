# Yamibo MCP 测试方案

> 基于 2026-06-21 真实论坛数据设计，覆盖黑盒和白盒测试策略。
> 所有的代码备注使用简体中文

## 1. 设计原则

### 1.1 高内聚，低耦合

- **模块隔离**：每个测试文件只测试一个模块/功能
- **依赖注入**：通过参数传递依赖，而非硬编码
- **接口隔离**：测试公开接口，不依赖内部实现

### 1.2 独立性与可重复性

- **无状态测试**：每个测试独立运行，不依赖其他测试
- **数据隔离**：使用临时数据库和临时目录
- **确定性输出**：使用固定的测试数据，不依赖外部状态

### 1.3 数据与逻辑分离

- **测试数据外置**：所有测试数据保存在 `tests/fixtures/` 目录
- **数据加载器**：统一的数据加载接口
- **数据版本化**：测试数据与代码版本同步

### 1.4 测试覆盖维度

不仅测试"能做什么"，还要测试：
- **边界条件**：空值、缺失字段、畸形数据
- **错误处理**：网络错误、权限错误、超时
- **并发场景**：竞态条件、死锁
- **性能边界**：大数据量、长时间运行

---

## 2. 测试规范

### 2.1 3A 原则 (Arrange, Act, Assert)

每个测试必须严格遵循三段式结构，用空行分隔各阶段：

```python
def test_archived_item_has_series_id():
    # Arrange — 准备数据和前置条件
    data = load_forum_page(1)
    archived = [i for i in data["items"] if i.get("archive_status") == "complete"]
    if not archived:
        pytest.skip("No archived items")
    item = archived[0]

    # Act — 执行被测行为
    series_id = item.get("series_id")

    # Act — 执行被测行为（无显式调用时，直接取值即可）
    # Assert — 验证结果
    assert series_id is not None
    assert isinstance(series_id, str)
```

**规则**：
- Arrange 只做数据加载和前置条件设置，不做断言
- Act 只做被测行为的触发，不做断言
- Assert 只做结果验证，不做数据准备
- 若 Act 阶段无显式函数调用（如直接读取字段），可合并 Act/Assert，但 Arrange 必须独立

### 2.2 参数化测试 (Parameterized Testing)

当多个测试用例仅输入数据不同时，**必须**使用 `@pytest.mark.parametrize` 而非复制测试函数：

```python
# ✓ 正确：参数化
@pytest.mark.parametrize("page", [1, 2, 3, 5, 10, 20])
def test_archived_items_have_series_id(page):
    data = load_forum_page(page)
    archived = [i for i in data["items"] if i.get("archive_status") == "complete"]
    if not archived:
        pytest.skip(f"Page {page} has no archived items")
    for item in archived:
        assert item.get("series_id") is not None
```

**规则**：
- 相同逻辑、不同输入 → `@pytest.mark.parametrize`
- 使用 `ids` 参数为用例命名，便于失败定位：`@pytest.mark.parametrize("tid", [...], ids=str)`
- 边界值和典型值分开参数化，不要混在同一个装饰器里

### 2.3 测试命名规范

测试函数和类的命名必须让失败信息一目了然：

```
test_<被测对象>_<行为或状态>
```

**示例**：

| 命名 | 含义 |
|------|------|
| `test_archived_items_have_series_id` | 已归档帖子应包含 series_id |
| `test_floor_no_is_sequential` | 楼层号应连续 |
| `test_empty_floor_thread` | 空楼层边界条件 |
| `test_title_parse_has_confidence` | 标题解析结果应包含置信度 |

**规则**：
- 使用 `test_` 前缀 + 小写蛇形命名
- 名称应描述**预期行为**，而非实现细节（`test_call_api` ✗ → `test_archived_items_have_series_id` ✓）
- 参数化用例通过 `ids` 参数标识输入：`@pytest.mark.parametrize("tid", [...], ids=str)`
- 测试类命名：`Test<被测模块>`，如 `TestForumListParsing`、`TestThreadDetailParsing`

### 2.4 Fixture 使用规范

**何时用 `@pytest.fixture`**：
- 多个测试共享同一份复杂数据或对象
- 需要自动清理的资源（临时目录、临时数据库）
- 需要注入的依赖（mock 对象、配置）

**何时用直接调用**：
- 简单数据加载（如 `load_forum_page(1)`）不需要 fixture，在 Arrange 阶段直接调用即可

**规则**：
- Fixture 放在 `conftest.py`（跨模块共享）或测试文件顶部（仅本文件使用）
- Fixture 命名用小写蛇形，不要加 `fixture_` 前缀
- 避免在 fixture 中写断言——fixture 只负责准备，不负责验证
- 使用 `tmp_path`（内置 fixture）处理临时文件，不要手动管理临时目录

```python
# ✓ 正确：fixture 提供共享数据，测试内直接调用加载器
@pytest.fixture
def sample_thread():
    return load_thread(572627)

def test_thread_has_floors(sample_thread):
    # Arrange — fixture 已提供数据
    # Act
    floors = sample_thread["floors"]
    # Assert
    assert len(floors) > 0

# ✓ 正确：简单加载无需 fixture
def test_archived_items_have_series_id(page):
    data = load_forum_page(page)  # 直接调用，无需 fixture
    ...
```

---

## 3. 测试架构

```
tests/
├── conftest.py                  # 全局配置（排除备份目录等）
├── fixtures/                    # 测试数据（与代码分离）
│   ├── loader.py               # 数据加载器
│   ├── forum_pages/             # 论坛列表页数据
│   │   └── page_{1,2,3,5,10,20}.json
│   ├── threads/                 # 帖子详情数据
│   │   └── thread_{tid}.json
│   ├── title_parses/            # 标题解析数据
│   │   └── {simple,with_group,...}.json
│   ├── content_shapes/          # 内容形态数据
│   │   └── {comic,novel,discussion,mixed}.json
│   └── edge_cases/              # 边界条件数据
│       ├── empty_floor.json     # 空楼层
│       ├── missing_fields.json  # 缺失字段
│       ├── malformed.json       # 畸形数据
│       └── single_floor.json    # 单层楼帖子
├── unit/                        # 单元测试（高内聚）
│   ├── test_parsers/            # 解析器测试
│   │   ├── test_forum_list.py
│   │   ├── test_thread_detail.py
│   │   └── test_title_parser.py
│   ├── test_db/                 # 数据仓库测试
│   │   ├── test_jobs_repository.py
│   │   ├── test_threads_repository.py
│   │   ├── test_series_repository.py
│   │   ├── test_audit_events_repository.py
│   │   ├── test_migrations.py
│   │   ├── test_content_blocks_repository.py
│   │   ├── test_assets_repository.py
│   │   ├── test_job_events.py
│   │   └── test_threads_forum_filter.py
│   ├── test_domain/             # 领域模型测试
│   │   ├── test_models.py
│   │   ├── test_validation.py
│   │   ├── test_forums.py
│   │   └── test_content.py
│   ├── test_storage/            # 存储层测试
│   ├── test_services/           # 服务层测试
│   ├── test_server/             # Server 层测试
│   │   ├── test_resources.py
│   │   ├── test_protocol_legacy.py
│   │   └── test_forum_id_tools.py
│   ├── test_application/        # 应用层测试
│   │   ├── test_contracts.py
│   │   ├── test_thread_use_cases.py
│   │   └── test_job_use_cases.py
│   └── test_yamibo/             # Yamibo 模块测试
├── integration/                 # 集成测试（待实现）
└── e2e/                         # 端到端测试（待实现）
```

---

## 4. 测试数据管理

### 4.1 数据获取脚本

```bash
# 获取真实论坛数据用于离线测试
uv run python scripts/fetch_fixtures.py
```

**获取的数据**：
- `tests/fixtures/forum_pages/`：论坛列表页（第 1/2/3/5/10/20 页）
- `tests/fixtures/threads/`：帖子详情（8 个不同特征的帖子）
- `tests/fixtures/title_parses/`：标题解析结果（6 种典型格式）
- `tests/fixtures/edge_cases/`：边界条件数据（空楼层、缺失字段、畸形数据、单层楼）

### 4.2 数据驱动测试 (DDT)

使用 `@pytest.mark.parametrize` 实现数据驱动测试：

```python
@pytest.mark.parametrize("page", [1, 2, 3, 5, 10, 20])
def test_archived_items_have_series_info(self, page):
    """已归档帖子应包含 series 信息"""
    data = load_forum_page(page)
    archived = [i for i in data["items"] if i.get("archive_status") == "complete"]
    if not archived:
        pytest.skip(f"Page {page} has no archived items")
    for item in archived:
        assert item.get("series_id") is not None
```

### 4.3 测试用例原子性

- 每个测试用例独立运行，不依赖其他测试
- 使用 `pytest.fixture` 提供测试数据
- 测试数据在测试结束后自动清理

### 4.4 数据加载器

```python
# tests/fixtures/loader.py
import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent

def load_fixture(name: str) -> dict[str, Any]:
    """加载测试数据"""
    path = FIXTURES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))

def load_forum_page(page: int) -> dict[str, Any]:
    """加载论坛列表页数据"""
    return load_fixture(f"forum_pages/page_{page}.json")

def load_thread(tid: int) -> dict[str, Any]:
    """加载帖子详情数据"""
    return load_fixture(f"threads/thread_{tid}.json")

def load_title_parse(name: str) -> dict[str, Any]:
    """加载标题解析数据"""
    return load_fixture(f"title_parses/{name}.json")

def load_edge_case(name: str) -> dict[str, Any]:
    """加载边界条件数据"""
    return load_fixture(f"edge_cases/{name}.json")
```

### 4.5 真实测试数据样本

**论坛列表页数据**（来源：`browse-forum-page`）

| 页码 | 记录数 | 特征 |
|------|--------|------|
| 第 1 页 | 50 条 | 最新帖子，包含已归档和未归档 |
| 第 2 页 | 60 条 | 包含已导出帖子 |
| 第 3 页 | 60 条 | 包含章节范围帖子（51~60） |
| 第 5 页 | 59 条 | 包含高回复帖子（110 条回复） |
| 第 10 页 | 59 条 | 包含特殊标题前缀（【!】） |
| 第 20 页 | 56 条 | 包含旧帖子（2026-04） |

**帖子详情数据**（来源：`get-thread`）

| TID | 特征 | 测试点 |
|-----|------|--------|
| 572627 | 简单帖子，2 层楼，24 张图 | 基本解析、图片计数 |
| 572617 | 13 层楼，有回复引用 | 楼层解析、回复引用 |
| 572458 | 摇曳百合 233，有章节名 | 章节号+章节名解析 |
| 569610 | 0 回复，只有 1 层楼 | 单层楼边界 |
| 571281 | 标题带【!】前缀 | 特殊字符处理 |
| 572427 | 章节范围 51~60 | 范围解析 |
| 572620 | 长标题，多作者 | LLM 解析 |

### 4.6 数据生成脚本

```bash
# 使用 CLI 命令获取数据（需要论坛 cookie）
uv run yamibo-mcp-server browse-forum-page --page 1 > tests/fixtures/forum_pages/page_1.json
uv run yamibo-mcp-server get-thread --tid 572627 > tests/fixtures/threads/thread_572627.json
uv run yamibo-mcp-server parse-thread-title "【超时空辉夜姬】[ポテトルス] ray" > tests/fixtures/title_parses/simple.json

# 或使用批量脚本
uv run python scripts/fetch_fixtures.py
```

---

## 5. 黑盒测试方案

### 5.1 论坛列表解析器测试（已实现）

**测试文件**：`tests/unit/test_parsers/test_forum_list.py`（57 个测试用例）

**测试内容**：
- 基本结构验证（页面结构、必需字段、ID 类型、URL 格式）
- 已归档帖子系列信息（series_id、series_key、resources）
- 未归档帖子无系列信息
- 已导出帖子导出路径
- 分类有效性、回复数非负
- 多页结构一致性
- 边界条件（空楼层、缺失字段）

**示例**：

```python
@pytest.mark.parametrize("page", [1, 2, 3, 5, 10, 20])
def test_archived_items_have_series_id(self, page):
    """已归档帖子应包含 series_id"""
    data = load_forum_page(page)
    archived = [i for i in data["items"] if i.get("archive_status") == "complete"]
    if not archived:
        pytest.skip(f"Page {page} has no archived items")
    for item in archived:
        assert item.get("series_id") is not None
```

### 5.2 帖子详情解析器测试（已实现）

**测试文件**：`tests/unit/test_parsers/test_thread_detail.py`（45 个测试用例）

**测试内容**：
- 基本结构验证（帖子结构、楼层列表、必需字段）
- 楼层号连续性、楼层计数一致性
- 图片计数非负
- 标题解析置信度和系列键
- 已归档帖子系列信息
- 特定帖子特征（多楼层、单楼层、章节名、特殊前缀、章节范围、导出）
- 边界条件（空楼层、缺失字段、畸形数据、单层楼）

### 5.3 标题解析器测试（已实现）

**测试文件**：`tests/unit/test_parsers/test_title_parser.py`（37 个测试用例）

**测试内容**：
- 简单标题解析（核心标题、作者、章节）
- 带汉化组标题解析（组名、作者、核心标题）
- 章节号+章节名解析
- 特殊前缀处理
- 复杂标题和短标题
- 置信度有效性、needs_review 布尔值、章节索引数字类型
- 结构一致性

---

## 6. 白盒测试方案（已实现）

### 6.1 数据仓库测试

**测试文件**：`tests/unit/test_db/`（97 个测试用例）

**已测试模块**：
- `test_jobs_repository.py` — Job CRUD、租约抢占、心跳、状态变更、过期恢复（26 用例）
- `test_threads_repository.py` — 帖子 upsert、标题解析、楼层、删除、导出标记、搜索（13 用例）
- `test_series_repository.py` — 系列创建/复用、别名累积、合并、删除、复核确认（13 用例）
- `test_audit_events_repository.py` — 审计事件记录、时间排序、limit（6 用例）
- `test_migrations.py` — 空库迁移、旧库迁移、幂等性、新表创建、列回填（15 用例）
- `test_content_blocks_repository.py` — 内容块 upsert、metadata JSON、替换、排序（7 用例）
- `test_assets_repository.py` — 资产 upsert、类型、状态、local path（10 用例）
- `test_job_events.py` — 事件追加、succeed/fail 事件、append 失败降级、list 过滤（14 用例）
- `test_threads_forum_filter.py` — search_threads/list_threads 按 forum_id 过滤（7 用例）

### 6.2 存储层测试

**测试文件**：`tests/unit/test_storage/`（40 个测试用例）

**已测试模块**：
- `test_atomic.py` — 原子写入、父目录创建、覆盖已有文件（3 用例）
- `test_paths.py` — 路径模板：series/thread/staging/exports（11 用例）
- `test_staging.py` — staging 快照/失败日志/标题解析日志写入（4 用例）
- `test_markdown.py` — Markdown 渲染（frontmatter/楼层/图片）、JSON 元数据渲染（10 用例）
- `test_exports.py` — 导出就绪检查、过期判断、ZIP 打包、完整性校验（12 用例）

### 6.3 服务层测试

**测试文件**：`tests/unit/test_services/`（9 个测试用例）

**已测试模块**：
- `test_title_hints.py` — 提示词加载/写入/更新、文件创建、去重、损坏 JSON 兜底（9 用例）

**待测试模块**（需 mock 外部 API）：
- `services/llm_client.py` — LLM 调用
- `services/title_llm.py` — LLM 标题解析

### 6.4 Domain 层测试

**测试文件**：`tests/unit/test_domain/`（35 个测试用例）

**已测试模块**：
- `test_validation.py` — 快照校验：tid/page_type/标题/楼层完整性/图片/warnings（20 用例）
- `test_models.py` — 枚举值完整性、JobID 格式与唯一性、数据类不可变性、默认值（15 用例）

### 6.5 Yamibo 模块测试

**测试文件**：`tests/unit/test_yamibo/`（51 个测试用例）

**已测试模块**：
- `test_page_classifier.py` — 页面分类：维护页/登录页/帖子详情/列表页/搜索结果/未知（16 用例）
- `test_normalizer.py` — 标题规范化：NFKC、繁简转换、标点清理、Discuz 后缀（15 用例）
- `test_content_cleaner.py` — 内容清洗：附件提示/文件名/时间戳/空白规范化（12 用例）
- `test_series_matcher.py` — 系列匹配：base_key/creator_key/alias/needs_review（8 用例）

---

## 7. 已知问题

### 7.1 备份目录需排除

`tests/unit/_backup_20260619/` 包含旧测试文件，引用了不存在的 `html_sample/` 目录。已在 `tests/conftest.py` 中配置排除。

### 7.2 LLM 解析结果可变性

标题解析使用 LLM，同一标题在不同运行中可能产生不同结果。测试数据使用固定 fixture 而非实时调用。

---

## 8. 测试执行

### 8.1 运行测试

```bash
# 运行所有测试
uv run pytest

# 运行单元测试
uv run pytest tests/unit/

# 运行集成测试
uv run pytest tests/integration/

# 运行特定测试文件
uv run pytest tests/unit/test_parsers/test_title_parser.py

# 运行特定测试
uv run pytest -k "test_simple_title"

# 查看覆盖率（需安装 pytest-cov）
uv run pytest --cov=yamibo_mcp --cov-report=html
```

### 8.2 测试数据更新

```bash
# 重新生成测试数据（需要论坛 cookie）
uv run python scripts/fetch_fixtures.py

# 验证测试数据
uv run pytest tests/unit/test_parsers/ -v
```

---

## 9. 覆盖率目标

| 模块 | 目标覆盖率 | 说明 |
|------|-----------|------|
| 解析器（parsers） | 95%+ | 核心业务逻辑 |
| 数据仓库（repositories） | 90%+ | 数据操作 |
| 服务（services） | 85%+ | 外部依赖 |
| 存储（storage） | 90%+ | 文件操作 |
| CLI（server） | 80%+ | 用户接口 |

---

## 10. 持续集成

### 10.1 GitHub Actions

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.13']
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: ${{ matrix.python-version }}
      - run: uv sync --extra dev
      - run: uv run pytest --cov=yamibo_mcp --cov-report=xml
```

### 10.2 本地开发

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest

# 运行测试并生成覆盖率报告
uv run pytest --cov=yamibo_mcp --cov-report=html

# 查看覆盖率报告
open htmlcov/index.html
```

---

## 11. 已知约束

### 11.1 论坛维护时间

- **时间**：每天 5:30-6:30（UTC+8）
- **影响**：访问论坛只能获取维护界面
- **缓解措施**：
  - 避开维护窗口进行集成测试
  - 使用本地 fixtures 进行离线测试
  - 维护期间返回特定错误（`RemoteMaintenanceError`）

### 11.2 搜索限流

- **限制**：15 秒/次
- **影响**：频繁搜索会被限流
- **缓解措施**：
  - 测试中添加延时（`time.sleep(15)`）
  - 使用本地 fixtures 进行离线测试
  - 避免在测试中频繁调用搜索接口

### 11.3 测试执行建议

```bash
# 正常时间运行测试（推荐）
uv run pytest tests/unit/

# 避开维护窗口运行集成测试
# 5:30-6:30 UTC+8 不要运行需要访问论坛的测试

# 搜索测试需要添加延时
def test_search_with_delay():
    time.sleep(15)  # 等待限流窗口
    result = search_threads("测试")
    assert result is not None
```
