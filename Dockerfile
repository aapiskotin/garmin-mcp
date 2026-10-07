FROM python:3.12-slim AS sqlite-build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY scripts/build_sqlite.py ./
RUN python build_sqlite.py

FROM python:3.12-slim AS base
COPY --from=sqlite-build /libsqlite3.so.0 /usr/local/lib/libsqlite3.so.0
RUN ldconfig

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev
RUN .venv/bin/python -c 'from garmin_mcp.db import require_patched_sqlite; require_patched_sqlite()'

ENV PATH="/app/.venv/bin:$PATH"

ENV GARMIN_MCP_TRANSPORT=streamable-http
ENV GARMIN_MCP_HOST=0.0.0.0
ENV GARMIN_MCP_PORT=8000
ENV GARMIN_TOKEN_DIR=/data/garmin-tokens

VOLUME ["/data/garmin-tokens"]
EXPOSE 8000

CMD ["garmin-mcp"]

FROM base AS test
RUN uv sync --locked --extra dev
COPY tests ./tests
CMD ["pytest"]

FROM base AS runtime
