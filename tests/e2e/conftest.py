"""E2E test configuration.

All tests in this package are gated behind an ``e2e`` pytest mark and are
skipped automatically when the API at ``E2E_API_URL`` is not reachable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest
import structlog

log = structlog.get_logger(__name__)

E2E_API_URL: str = os.environ.get("E2E_API_URL", "http://localhost:8000")
E2E_DASHBOARD_URL: str = os.environ.get("E2E_DASHBOARD_URL", "http://localhost:3000")


def _api_reachable() -> bool:
    """Return True if the API's /healthz responds within a short timeout."""
    try:
        resp = httpx.get(f"{E2E_API_URL}/healthz", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "e2e: mark test as an end-to-end integration test against a live stack",
    )


@pytest.fixture(scope="session", autouse=True)
def require_api() -> None:
    """Skip the entire session when the API is not reachable."""
    if not _api_reachable():
        pytest.skip(
            f"E2E API not reachable at {E2E_API_URL} — "
            "start the stack with `docker compose -f infra/docker-compose.e2e.yml up` "
            "or set E2E_API_URL to point at a running instance.",
            allow_module_level=True,
        )


@pytest.fixture(scope="session")
def api_url() -> str:
    return E2E_API_URL


@pytest.fixture(scope="session")
def dashboard_url() -> str:
    return E2E_DASHBOARD_URL


@pytest.fixture(scope="session")
def http() -> Iterator[httpx.Client]:
    """Shared httpx client for the whole E2E session."""
    with httpx.Client(base_url=E2E_API_URL, timeout=10.0) as client:
        yield client
