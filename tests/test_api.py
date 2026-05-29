"""Integration tests for service registration and contract upload."""

from __future__ import annotations

import base64
import hashlib
import json

from fastapi.testclient import TestClient


def _openapi_spec(title: str = "demo") -> dict[str, object]:
    return {
        "openapi": "3.0.0",
        "info": {"title": title, "version": "1.0.0"},
        "paths": {},
    }


def test_create_service_returns_201(client: TestClient) -> None:
    r = client.post("/services", json={"name": "billing", "owner": "team-pay"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "billing"
    assert body["owner"] == "team-pay"
    assert body["id"]


def test_duplicate_service_returns_409(client: TestClient) -> None:
    payload = {"name": "billing", "owner": "team-pay"}
    assert client.post("/services", json=payload).status_code == 201
    assert client.post("/services", json=payload).status_code == 409


def test_upload_openapi_contract_returns_201_with_hash(client: TestClient) -> None:
    svc = client.post("/services", json={"name": "billing", "owner": "team-pay"}).json()
    spec = _openapi_spec()
    expected_hash = hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    r = client.post(
        f"/services/{svc['id']}/contracts",
        json={"name": "billing-api", "kind": "openapi", "spec": spec},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] is True
    assert body["kind"] == "openapi"
    assert body["version"]["version_hash"] == expected_hash
    assert body["version"]["service_id"] == svc["id"]


def test_upload_openapi_contract_is_idempotent(client: TestClient) -> None:
    svc = client.post("/services", json={"name": "billing", "owner": "team-pay"}).json()
    payload = {"name": "billing-api", "kind": "openapi", "spec": _openapi_spec()}
    first = client.post(f"/services/{svc['id']}/contracts", json=payload)
    assert first.status_code == 201
    second = client.post(f"/services/{svc['id']}/contracts", json=payload)
    assert second.status_code == 201
    assert second.json()["created"] is False
    assert first.json()["version"]["id"] == second.json()["version"]["id"]
    assert first.json()["version"]["version_hash"] == second.json()["version"]["version_hash"]


def test_upload_proto_contract_uses_raw_bytes(client: TestClient) -> None:
    svc = client.post("/services", json={"name": "orders", "owner": "team-pay"}).json()
    raw = b"\x0a\x05hello"  # bogus FileDescriptorSet bytes are fine here
    spec_b64 = base64.b64encode(raw).decode()
    expected_hash = hashlib.sha256(raw).hexdigest()
    r = client.post(
        f"/services/{svc['id']}/contracts",
        json={"name": "orders.proto", "kind": "proto", "spec_b64": spec_b64},
    )
    assert r.status_code == 201, r.text
    assert r.json()["version"]["version_hash"] == expected_hash


def test_upload_contract_unknown_service_returns_404(client: TestClient) -> None:
    r = client.post(
        "/services/does-not-exist/contracts",
        json={"name": "billing-api", "kind": "openapi", "spec": _openapi_spec()},
    )
    assert r.status_code == 404


def test_openapi_without_spec_is_422(client: TestClient) -> None:
    svc = client.post("/services", json={"name": "billing", "owner": "team-pay"}).json()
    r = client.post(
        f"/services/{svc['id']}/contracts",
        json={"name": "billing-api", "kind": "openapi"},
    )
    assert r.status_code == 422


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_healthz_returns_version_and_probes(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"version", "git_sha", "db_ok", "redis_ok"}
    assert body["version"]
    assert isinstance(body["db_ok"], bool)
    assert isinstance(body["redis_ok"], bool)
    # The migrated SQLite DB used by tests is always reachable; Redis may
    # not be, but the probe must still return a valid boolean.
    assert body["db_ok"] is True
