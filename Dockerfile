FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH"

ENV GARMIN_MCP_TRANSPORT=streamable-http
ENV GARMIN_MCP_HOST=0.0.0.0
ENV GARMIN_MCP_PORT=8000
ENV GARMIN_TOKEN_DIR=/data/garmin-tokens

VOLUME ["/data/garmin-tokens"]
EXPOSE 8000

CMD ["garmin-mcp"]
