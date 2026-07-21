# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.14.6-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.30

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /build

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM builder AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-groups --no-install-project
COPY src ./src
COPY tests ./tests
ENV PATH="/build/.venv/bin:${PATH}" \
    PYTHONPATH=/build/src \
    RUFF_CACHE_DIR=/tmp/ruff-cache
CMD ["sh", "-c", "ruff format --check . && ruff check . && pytest -o cache_dir=/tmp/pytest-cache"]

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_VERSION=0.1.0

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app

WORKDIR /app
COPY --from=builder --chown=10001:10001 /build/.venv /app/.venv
COPY --chown=10001:10001 src ./src
COPY --chown=10001:10001 granian-log.json ./granian-log.json

ENV APP_VERSION=${APP_VERSION} \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=2s --start-period=5s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=1).read()"]

ENTRYPOINT ["python", "-m", "granian"]
CMD ["--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--backpressure", "64", "--workers-kill-timeout", "30s", "--respawn-failed-workers", "--log-level", "info", "--log-config", "/app/granian-log.json", "--no-access-log", "observability_demo.main:app"]
