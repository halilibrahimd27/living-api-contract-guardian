"""FastAPI entrypoint for the Guardian API."""

from __future__ import annotations

from fastapi import FastAPI
from guardian_core.logging import configure_logging, get_logger

from apps.api.routes.services import router as services_router


def create_app() -> FastAPI:
    """Application factory."""
    configure_logging()
    app = FastAPI(title="Living API Contract Guardian", version="0.1.0")
    app.include_router(services_router)

    log = get_logger("api")

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        log.debug("health.ping")
        return {"status": "ok"}

    return app


app = create_app()
