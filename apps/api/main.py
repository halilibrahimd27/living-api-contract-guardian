"""FastAPI entrypoint for the Guardian API."""

from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from guardian_core.db import get_engine
from guardian_core.logging import configure_logging, get_logger
from guardian_core.redis_client import ping_redis
from guardian_core.schemas import HealthResponse
from guardian_core.version import get_git_sha, get_version
from sqlalchemy import text
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from apps.api.routes.campaigns import router as campaigns_router
from apps.api.routes.ci import router as ci_router
from apps.api.routes.diff import router as diff_router
from apps.api.routes.guides import router as guides_router
from apps.api.routes.ingest import router as ingest_router
from apps.api.routes.services import router as services_router

# Auth env var. When set, every request outside the health + docs surface
# must present this key. Unset (the default) leaves the API open, preserving
# the self-hosted / local-dev posture.
_API_KEY_ENV = "GUARDIAN_API_KEY"
# Exact paths reachable without an API key even when one is configured.
# Matched exactly (never by prefix) so a future route such as "/docs-export"
# can't be accidentally exempted.
_AUTH_PUBLIC_PATHS = frozenset(
    {"/health", "/healthz", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
)


def _is_public_path(path: str) -> bool:
    """Paths reachable without an API key even when one is configured."""
    return path in _AUTH_PUBLIC_PATHS


def _extract_api_key(request: Request) -> str | None:
    """Pull the caller's key from ``X-API-Key`` or ``Authorization: Bearer``."""
    header = request.headers.get("x-api-key")
    if header:
        return header
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


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
    app.include_router(ingest_router)
    app.include_router(diff_router)
    app.include_router(ci_router)
    app.include_router(guides_router)
    app.include_router(campaigns_router)

    log = get_logger("api")

    @app.middleware("http")
    async def require_api_key(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Opt-in API-key gate.

        Inert unless ``GUARDIAN_API_KEY`` is set, so the default posture is
        unchanged. When configured, every request except the health + docs
        surface must present the key via ``X-API-Key`` or
        ``Authorization: Bearer <key>``. The compare is constant-time so the
        key cannot be recovered by timing the 401 response.
        """
        configured_key = os.environ.get(_API_KEY_ENV)
        if configured_key and not _is_public_path(request.url.path):
            provided = _extract_api_key(request)
            # Compare on bytes: Starlette decodes header values as latin-1, so a
            # str compare would raise TypeError on any non-ASCII byte and turn a
            # rejected request into a 500. latin-1 round-trips the wire bytes
            # exactly (ASCII + 0x80-0xFF keys still match); "ignore" keeps an
            # exotic configured key from crashing instead of simply not matching.
            if provided is None or not hmac.compare_digest(
                provided.encode("latin-1", "ignore"),
                configured_key.encode("latin-1", "ignore"),
            ):
                log.warning("api.auth.rejected", path=request.url.path)
                return JSONResponse(
                    status_code=401,
                    content={"detail": "missing or invalid API key"},
                )
        return await call_next(request)

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
