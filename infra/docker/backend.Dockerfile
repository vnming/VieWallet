# Backend image (API, worker and migration jobs share it).
# Build context: repository root.
FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /srv/backend

FROM base AS deps
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked uv sync --frozen --no-dev --no-install-project

FROM base AS runtime
RUN groupadd --system app && useradd --system --gid app --home /srv app
COPY --from=deps /opt/venv /opt/venv
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/app ./app
# The provider simulator ships in the same image but runs as its own service (ADR-0005).
COPY backend/sim_alembic.ini ./
COPY backend/sim ./sim
ENV PATH="/opt/venv/bin:$PATH"
USER app
EXPOSE 8000
CMD ["uvicorn", "--factory", "app.main:app_factory", "--host", "0.0.0.0", "--port", "8000"]
