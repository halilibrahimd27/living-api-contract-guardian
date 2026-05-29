"""FastAPI entrypoint for the Guardian API."""

from __future__ import annotations

from fastapi import FastAPI
from guardian_core.db import get_engine
from guardian_core.logging import configure_logging, get_logger
from guardian_core.redis_client import ping_redis
from guardian_core.schemas import HealthResponse
from guardian_core.version import get_git_sha, get_version
from sqlalchemy import text

from apps.api.routes.services import router as services_router


def _ping_database() -> bool:
    """Best-effort ``SELECT 1`` against the configured database."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


def create_app() -> FastAPI:
    """Application factory."""
    configure_logging()
    version = get_version()
    app = FastAPI(title="Living API Contract Guardian", version=version)
    app.include_router(services_router)

    log = get_logger("api")

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        log.debug("health.ping")
        return {"status": "ok"}

    @app.get("/healthz", response_model=HealthResponse, tags=["health"])
    def healthz() -> HealthResponse:
        """Deep health probe: includes db + redis connectivity."""
        db_ok = _ping_database()
        redis_ok = ping_redis()
        payload = HealthResponse(
            version=version,
            git_sha=get_git_sha(),
            db_ok=db_ok,
            redis_ok=redis_ok,
        )
        log.debug("healthz.ping", db_ok=db_ok, redis_ok=redis_ok)
        return payload

    return app


app = create_app()
