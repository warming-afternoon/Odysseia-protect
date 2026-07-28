# syntax=docker/dockerfile:1.7

# ============================================================
# python-base — 公共 Python 运行环境与 uv 配置
# ============================================================
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# ============================================================
# runtime-deps — 仅安装 Bot 运行时第三方依赖
# ============================================================
FROM python-base AS runtime-deps

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ============================================================
# dev-deps — 安装全部第三方依赖（含测试/迁移所需）
# ============================================================
FROM python-base AS dev-deps

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# ============================================================
# base — 安装项目源码，用于正常启动 Bot
# ============================================================
FROM runtime-deps AS base

COPY alembic.ini migrate.py ./
COPY alembic/ ./alembic/
COPY src/ ./src/
COPY main.py ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ============================================================
# dev — 安装项目源码和全部依赖，用于迁移数据库等
# ============================================================
FROM dev-deps AS dev

COPY alembic.ini migrate.py ./
COPY alembic/ ./alembic/
COPY src/ ./src/
COPY main.py ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen
