FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Copy vendored wheels first so offline CI/Docker builds can install from
# them via --find-links.
COPY vendor/wheels /vendor/wheels

COPY pyproject.toml ./
COPY README.md ./
COPY packages ./packages
COPY apps ./apps
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

RUN pip install --upgrade pip && \
    pip install --find-links=/vendor/wheels . && \
    pip install --find-links=/vendor/wheels "uvicorn[standard]>=0.27"

ENV PYTHONPATH=/app:/app/packages:/app/apps

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=20 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["sh", "-c", "guardian migrate && uvicorn apps.api.main:app --host 0.0.0.0 --port 8000"]
