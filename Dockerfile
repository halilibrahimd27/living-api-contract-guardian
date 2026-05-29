FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install \
      "fastapi>=0.110" "uvicorn[standard]>=0.27" \
      "sqlalchemy>=2.0.29" "alembic>=1.13" "psycopg2-binary>=2.9" \
      "pydantic>=2.6" "pydantic-settings>=2.2" \
      "structlog>=24.1" "redis>=5.0" "httpx>=0.27"

COPY . ./

ENV PYTHONPATH=/app:/app/packages:/app/apps

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn apps.api.main:app --host 0.0.0.0 --port 8000"]
