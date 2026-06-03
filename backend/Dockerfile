FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# asyncpg speaks the Postgres wire protocol directly, so libpq-dev isn't
# needed. build-essential covers any sdist that lacks a slim wheel.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .

# Drop root — the app needs no elevated privileges at runtime.
RUN useradd --system --no-create-home --uid 10001 appuser \
    && chown -R appuser /app
USER appuser

# Migrations + uvicorn. We run alembic on startup so a fresh database
# converges to the latest schema without an extra deploy step.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
