# Graphiti MCP is built from a pinned upstream tag. FalkorDB is external and
# contains only a rebuildable projection of PostgreSQL session knowledge.
FROM alpine/git:2.49.1 AS source
ARG GRAPHITI_VERSION=v0.29.3
RUN git clone --depth 1 --branch "${GRAPHITI_VERSION}" \
    https://github.com/getzep/graphiti.git /src

FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin:${PATH}" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    MCP_SERVER_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1

WORKDIR /app/mcp
ARG GRAPHITI_CORE_VERSION=0.29.3
COPY --from=source /src/mcp_server/ ./
# FalkorDB 1.6 requires redis.driver_info, while redis 8.1 currently leaks
# himport_registry into its synchronous probe. Redis 8.0 is the compatible range.
RUN sed -i '/\[tool\.uv\.sources\]/,/graphiti-core/d' pyproject.toml \
    && sed -i "s/graphiti-core\[falkordb\][>=]\+[0-9]\+\.[0-9]\+\.[0-9]\+/graphiti-core[neo4j,falkordb]==${GRAPHITI_CORE_VERSION}/" pyproject.toml \
    && rm -f .python-version \
    && rm -f uv.lock \
    && uv add --no-sync 'redis>=8.0,<8.1' \
    && uv sync --no-group dev \
    && echo "${GRAPHITI_CORE_VERSION}" > /app/mcp/.graphiti-core-version \
    && mkdir -p /var/log/graphiti

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["uv", "run", "--no-sync", "main.py"]
