FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY scripts ./scripts
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" CAMPUS_RUNTIME=/data
CMD ["campus", "app", "--host", "0.0.0.0"]
