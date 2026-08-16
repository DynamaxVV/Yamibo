ARG PYTHON_IMAGE=python:3.12-slim

# --- 前端构建阶段 (保持原样，这部分通常缓存良好) ---
FROM node:20-slim AS frontend-build
WORKDIR /app/c
COPY c/package.json c/package-lock.json ./
RUN npm ci
COPY c ./
COPY src/yamibo_mcp/web/static ./src/yamibo_mcp/web/static
RUN npm run build


# --- 运行阶段 ---
FROM ${PYTHON_IMAGE} AS runtime

# 【优化 1】：引入 uv（用 Rust 编写的极速 Python 包管理器，替代慢吞吞的 pip）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}" \
    # 注意：使用 uv 时，不需要 PIP_NO_CACHE_DIR，因为我们要主动利用缓存
    YAMIBO_DATA_DIR=/app/data \
    YAMIBO_CONFIG_PATH=/app/data/yamibo.local.json \
    YAMIBO_WEB_HOST=0.0.0.0 \
    YAMIBO_WEB_PORT=8765 \
    YAMIBO_MCP_HOST=0.0.0.0 \
    YAMIBO_MCP_PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        build-essential

# 先拷贝项目基础文件
COPY pyproject.toml uv.lock README.md ./

# 【优化 2】：分层拷贝。先把最常变动的业务代码拷贝进来
COPY src ./src
COPY alembic ./alembic
COPY --from=frontend-build /app/src/yamibo_mcp/web/static ./src/yamibo_mcp/web/static

# 兼容不支持 BuildKit cache mount 的旧版 Docker。
RUN uv sync --locked --no-dev --no-editable \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 yamibo \
    && mkdir -p /app/data \
    && chown -R yamibo:yamibo /app/data

USER yamibo

EXPOSE 8765

CMD ["yamibo-daemon"]
