"""Redis connectivity helpers used by ``/healthz`` and worker tasks."""

from __future__ import annotations

import os

import redis


def get_redis_url() -> str:
    """Return the configured Redis URL.

    Falls back to ``redis://localhost:6379/0`` so the API can boot in dev.
    """
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def make_redis_client(url: str | None = None) -> redis.Redis[bytes]:
    """Build a ``redis.Redis`` client from a URL.

    A short socket timeout is applied so failed health checks do not stall
    request processing.
    """
    client: redis.Redis[bytes] = redis.Redis.from_url(
        url or get_redis_url(),
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )
    return client


def ping_redis(client: redis.Redis[bytes] | None = None) -> bool:
    """Return ``True`` if a Redis ``PING`` succeeds, ``False`` otherwise."""
    rc = client or make_redis_client()
    try:
        result = rc.ping()
    except Exception:
        return False
    return bool(result)
