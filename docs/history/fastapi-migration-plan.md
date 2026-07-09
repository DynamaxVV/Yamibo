# FastAPI 迁移 + 构建自动化 + env 覆盖 实施方案

> **状态：已完成 (2026-07-02)** | 65 个端点全部迁移，973 个测试通过

## Context

当前 Yamibo 项目 web 层基于 Python 标准库 `http.server.ThreadingHTTPServer`，路由通过 130 行 if-elif 链分发。由此带来三个问题：
1. **类型安全缺口**：后端用裸字典返回 JSON，前端 TypeScript 类型无自动同步机制
2. **构建产物路径耦合**：`npm run build` 输出直接写入 Python 源码树 `web/static/`，构建顺序无自动化
3. **路由维护困难**：字符串切片提取参数 (`route[9:-7]`)、分支顺序决定匹配优先级，每加路由都累积风险

## 目标

- 用 FastAPI + uvicorn 替换手写 HTTP server + if-elif 路由，获得自动参数校验、OpenAPI 文档、类型安全
- 自动化前端构建：`pip install` 时自动跑 `npm run build`（失败不阻断）
- 支持 `YAMIBO_STATIC_DIR` 环境变量覆盖静态文件路径

---

## 一、FastAPI 应用结构

### 目录布局（新目录 `web_fastapi/`，与旧 `web/` 并行）

```
src/yamibo_mcp/
  web/                          # 旧 Web 层（保留，PR6 删除）
  web_fastapi/
    __init__.py
    app.py                      # FastAPI app 工厂 + EmbeddedWebServer
    deps.py                     # Depends: get_settings, get_conn
    helpers.py                  # TTLCache (从 _helpers.py 迁移)
    converters.py               # *_to_dict() 转换函数 (从 _converters.py 迁移)
    models/
      __init__.py
      common.py                 # ErrorResponse
      requests.py               # 所有 POST body 的 Pydantic model
    routers/
      __init__.py
      jobs.py                   # APIRouter(prefix="/api/jobs")
      threads.py
      series.py
      forums.py
      rag.py
      review.py
      settings.py
      dashboard.py
      debug.py
      daemon.py
      remote_forum.py
      media.py                  # /media, /fonts, /artifacts 文件服务
    static_files.py             # StaticFiles mount + SPA fallback
```

### DB 连接：用 `Depends` (per-request 生命周期)

```python
# deps.py
def get_settings(request: Request) -> Settings:
    return request.app.state.settings

def get_conn(settings = Depends(get_settings)):
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()
```

### Settings 注入：`app.state.settings`

`create_app(settings)` 中设置 `app.state.settings = settings`，所有路由通过 `Depends(get_settings)` 获取。

### 旧 `_helpers.py` 的适配

| 旧函数 | FastAPI 替代 |
|--------|-------------|
| `json_response(handler, data)` | `return data`（FastAPI 自动 JSON 序列化） |
| `read_json_body(handler)` | `body: PydanticModel`（FastAPI 自动解析+校验） |
| `error_response(handler, msg, status)` | `raise HTTPException(status, detail=msg)` |
| `json_default(value)` | 不需要，FastAPI `jsonable_encoder` 内置支持 |
| `TTLCache` | 保留，移到 `helpers.py` |

### 旧 `_converters.py` 的处理

所有 `*_to_dict()` 函数保留，路由直接返回 dict。**渐进式用 Pydantic 替换**：先只对 POST body 定义 model，响应暂保持 dict。

---

## 二、非 API 路由

### 静态文件：`YAMIBO_STATIC_DIR` env 覆盖

```python
# static_files.py
def _resolve_static_dir() -> Path:
    env_dir = os.environ.get("YAMIBO_STATIC_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "web" / "static"

def mount_static_files(app: FastAPI, settings: Settings) -> None:
    static_dir = _resolve_static_dir()
    # 先 mount /assets（带 hash 的构建产物）
    # 最后注册 catch-all SPA fallback: 文件存在返回文件，否则回退 index.html
```

### media/fonts/artifacts：独立 Router + 路径遍历保护

现有 `_safe_resolve()` 逻辑不动，只是从 `WebHandler` 方法提取为路由函数，用 `FileResponse` 返回。

### 路由注册顺序（重要）

1. 所有 `APIRouter`（`/api/*`）
2. `/media`、`/fonts`、`/artifacts/jobs` 的 Router
3. `/assets` StaticFiles mount
4. 最后的 `/{full_path:path}` SPA fallback

---

## 三、Pydantic Model 策略

### 渐进式：请求 Body 全模型化，响应保持 dict

**PR2-PR4**：所有 POST 端点定义 Pydantic 请求 model（自动校验 + 文档）
**PR5+**：对高频端点（`/dashboard`、`/jobs`、`/settings`）定义响应 model

### Body model 示例

```python
# models/requests.py
class JobActionRequest(BaseModel):
    job_id: str = Field(..., min_length=1)

class JobControlRequest(BaseModel):
    action: Literal["pause", "resume"]

class ThreadsListParams(BaseModel):
    q: str | None = None
    forum_id: int | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    # ...
```

---

## 四、EmbeddedWebServer 重构

```python
# app.py
class EmbeddedWebServer:
    def __init__(self, settings: Settings):
        self._app = create_app(settings)
        self._config = uvicorn.Config(
            self._app, host=settings.web_host, port=settings.web_port,
            log_level="warning",
        )
        self._server = uvicorn.Server(self._config)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
```

`daemon/main.py` 只需改 import 路径。接口（`start/stop`）保持一致。

---

## 五、构建自动化

### 方案：自定义 build hook（失败不阻断）

```python
# build_helper.py
def build_frontend():
    """Run npm ci && npm run build; warn but don't fail on error."""
    # 1. 检查是否有 package.json
    # 2. 如果 static/ 已有产物，跳过
    # 3. 如果没有 node_modules，npm ci
    # 4. npm run build
    # 所有失败 print 警告后继续，不抛异常
```

### pyproject.toml 修改

```toml
[project]
dependencies = [
  # 现有依赖...
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
]

[project.scripts]
yamibo-build-frontend = "yamibo_mcp.build_helper:main"
```

- 对于 `pip install` 用户：预构建 wheel 已包含 `static/**/*`，不需要本地前端构建
- 对于开发者：`yamibo-build-frontend` 手动执行，或在 CI/Makefile 中串联
- `mcp` 包的 starlette 依赖与 FastAPI 的 starlette 依赖由 pip 依赖解析器自动处理

---

## 六、迁移步骤（6 个 PR）

| PR | 内容 | 风险 | 状态 |
|----|------|------|------|
| **PR1** | 新建 `web_fastapi/` 骨架（app、deps、static_files、EmbeddedWebServer）+ daemon `--web-backend` 参数，默认 `legacy` | 低 | ✅ 已完成 |
| **PR2** | 迁移 jobs + dashboard routers + Pydantic models + 测试 | 低 | ✅ 已完成 |
| **PR3** | 迁移 threads + series + forums routers | 低 | ✅ 已完成 |
| **PR4** | 迁移 rag + review + settings routers + media/files routers | 低 | ✅ 已完成 |
| **PR5** | 迁移 debug + daemon + remote_forum + knowledge routers；daemon 默认切换为 `fastapi` | 中 | ✅ 已完成 |
| **PR6** | 删除旧 `web/api.py`、`web/routes/`；重写 `web/app.py` 使用 FastAPI；更新 `pyproject.toml`；迁移 `log_buffer` 至 `web_fastapi/` | 中 | ✅ 已完成 |

### 回滚：`web/static/` 构建产物和 `yamibo-web` CLI 入口保留

### 开发期间：`web/` 和 `web_fastapi/` 并行

开发时可通过 `yamibo-daemon --web-backend fastapi` 测试新层，旧层始终可用。

---

## 七、测试策略

### 迁移方式

| 旧模式 | 新模式 |
|--------|--------|
| `_CaptureHandler` mock | `TestClient(app)` |
| `handler.wfile.getvalue()` → `json.loads()` | `response.json()` |
| `handler.rfile = BytesIO(body)` | `client.post(..., json={...})` |
| 直接调 handler 函数 | `client.get("/api/...")` 通过 HTTP |

### conftest 补充

```python
# tests/unit/test_web_fastapi/conftest.py
@pytest.fixture
def app(monkeypatch, tmp_path):
    test_settings = SimpleNamespace(data_dir=..., db_path=..., ...)
    return create_app(test_settings)

@pytest.fixture
def client(app):
    return TestClient(app)
```

### 新增测试类型

- Pydantic 验证：无效 body 返回 422
- SPA fallback：非 API 路径返回 index.html
- `YAMIBO_STATIC_DIR` 覆盖
- OpenAPI schema 快照（确保不意外 break API）

---

## 八、文件变更清单

### 新建
```
src/yamibo_mcp/web_fastapi/__init__.py
src/yamibo_mcp/web_fastapi/app.py
src/yamibo_mcp/web_fastapi/deps.py
src/yamibo_mcp/web_fastapi/helpers.py
src/yamibo_mcp/web_fastapi/converters.py
src/yamibo_mcp/web_fastapi/static_files.py
src/yamibo_mcp/web_fastapi/models/__init__.py
src/yamibo_mcp/web_fastapi/models/common.py
src/yamibo_mcp/web_fastapi/models/requests.py
src/yamibo_mcp/web_fastapi/routers/__init__.py
src/yamibo_mcp/web_fastapi/routers/{jobs,threads,series,forums,rag,review,settings,dashboard,debug,daemon,remote_forum,media}.py
src/yamibo_mcp/build_helper.py
tests/unit/test_web_fastapi/__init__.py
tests/unit/test_web_fastapi/conftest.py
tests/unit/test_web_fastapi/test_*.py
```

### 修改
```
pyproject.toml                      # 添加 fastapi, uvicorn 依赖 + build_helper 入口
src/yamibo_mcp/daemon/main.py       # --web-backend 参数
```

### 最终删除（PR6）
```
src/yamibo_mcp/web/app.py
src/yamibo_mcp/web/api.py
src/yamibo_mcp/web/routes/*.py     // 保留 _helpers.py 中的 TTLCache 和 _converters.py（已迁移到 web_fastapi/）
```

---

## 九、验证方案

1. **单元测试**：`uv run pytest tests/unit/test_web_fastapi/ -v`，所有迁移后的路由测试通过
2. **API 契约**：`uv run python -c "from yamibo_mcp.web_fastapi.app import create_app; print(create_app(settings).openapi())"` 生成 JSON schema，与前端 `client.ts` 中的 TypeScript 接口人工对比
3. **集成测试**：`yamibo-daemon --web-backend fastapi` 启动后，访问 `http://localhost:8765/api/dashboard` 返回与旧层一致的 JSON
4. **前端联调**：`cd frontend && npm run dev`，Vite proxy 指向 `localhost:8765`（FastAPI），功能正常
5. **SPA fallback**：直接访问 `http://localhost:8765/settings` 返回 index.html（非 404）
6. **构建自动化**：删除 `frontend/node_modules` 后 `uv pip install -e .`，构建产物仍存在（由 yamibo-build-frontend 补全）
