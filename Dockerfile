FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# 1. 设置环境变量
# UV_COMPILE_BYTECODE=1: 编译 pyc，启动更快
# UV_LINK_MODE=copy: 在某些容器文件系统中，复制模式比硬链接更稳
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# 2. 复制依赖文件
COPY pyproject.toml uv.lock ./

# 3. 安装依赖
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# 4. 将 uv 创建的虚拟环境加入 PATH
ENV PATH="/app/.venv/bin:$PATH"

# 5. 复制项目代码
COPY . .

# 6. 启动
CMD ["python", "main.py"]