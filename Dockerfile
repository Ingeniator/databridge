FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable

# ---- test stage: adds dev deps + tests on top of builder ----
FROM builder AS test

RUN uv sync --frozen --no-editable
COPY alembic.ini config.yaml.example ./
COPY tests/ tests/

CMD ["uv", "run", "pytest", "tests/", "-v", "--tb=short"]

# ---- runtime stage ----
FROM python:3.13-slim AS runtime

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY src/databridge/db/migrations/ src/databridge/db/migrations/
COPY config.yaml.example ./config.yaml.example

ENV PATH="/app/.venv/bin:$PATH" \
    DATABRIDGE_CONFIG="/app/config.yaml" \
    PYTHONUNBUFFERED="1"

EXPOSE 5010

CMD ["uvicorn", "databridge.main:app", "--host", "0.0.0.0", "--port", "5010"]
