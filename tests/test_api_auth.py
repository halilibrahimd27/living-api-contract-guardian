"""Opt-in API-key middleware (``GUARDIAN_API_KEY``).

The gate is inert unless the env var is set, so every other test in the
suite — which never sets it — is unaffected. These tests exercise both
postures explicitly.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


async def _status_via_raw_asgi(app: object, path: str, headers: list[tuple[bytes, bytes]]) -> int:
    """Drive a GET straight through the ASGI app and return the status code.

    Bypasses the httpx ``TestClient``, which ASCII-encodes outgoing header
    values and therefore cannot carry the non-ASCII bytes this test needs.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }
    captured: dict[str, int] = {}

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            captured["status"] = int(message["status"])  # type: ignore[arg-type]

    await app(scope, receive, send)  # type: ignore[operator]
    return captured["status"]


class TestApiKeyDisabled:
    """With no key configured the API stays open (default posture)."""

    def test_protected_route_open_without_key(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GUARDIAN_API_KEY", raising=False)
        assert client.get("/services").status_code == 200
        assert client.get("/health").status_code == 200


class TestApiKeyEnabled:
    """With a key configured everything outside health + docs needs it."""

    KEY = "s3cret-key-value"

    def test_health_and_docs_stay_public(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GUARDIAN_API_KEY", self.KEY)
        assert client.get("/health").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/docs").status_code == 200

    def test_missing_key_is_rejected(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GUARDIAN_API_KEY", self.KEY)
        resp = client.get("/services")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "missing or invalid API key"

    def test_wrong_key_is_rejected(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GUARDIAN_API_KEY", self.KEY)
        resp = client.get("/services", headers={"X-API-Key": "not-the-key"})
        assert resp.status_code == 401

    def test_correct_key_header_is_accepted(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GUARDIAN_API_KEY", self.KEY)
        resp = client.get("/services", headers={"X-API-Key": self.KEY})
        assert resp.status_code == 200

    def test_correct_bearer_token_is_accepted(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GUARDIAN_API_KEY", self.KEY)
        resp = client.get("/services", headers={"Authorization": f"Bearer {self.KEY}"})
        assert resp.status_code == 200

    async def test_non_ascii_key_header_returns_401_not_500(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A header byte in 0x80-0xFF must yield a clean 401, never a 500.

        ``hmac.compare_digest`` raises ``TypeError`` on non-ASCII *str*
        operands, and Starlette decodes header bytes as latin-1 — so the gate
        must compare on bytes. The httpx TestClient can't send non-ASCII
        header bytes, hence the raw-ASGI drive. Regression guard.
        """
        monkeypatch.setenv("GUARDIAN_API_KEY", self.KEY)
        from apps.api.main import create_app

        app = create_app()
        status = await _status_via_raw_asgi(app, "/services", [(b"x-api-key", b"\xe9\xff")])
        assert status == 401
