"""Integration tests for the ``POST /ci/runs`` and lookup endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _payload(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "repo": "acme/users",
        "pr_number": 42,
        "head_sha": "deadbeefcafebabe",
        "base_sha": "0123456789abcdef",
        "conclusion": "failure",
        "report_json": {
            "contract_kind": "openapi",
            "summary": {"total": 1, "breaking": 1, "behavioral": 0, "additive": 0},
            "changes": [],
            "spectral_findings": [],
            "ruleset_id": "default",
        },
        "bypass_label_present": False,
        "check_run_id": 1234567,
    }
    base.update(over)
    return base


def test_ci_run_create_persists_row(client: TestClient) -> None:
    r = client.post("/ci/runs", json=_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["repo"] == "acme/users"
    assert body["pr_number"] == 42
    assert body["head_sha"] == "deadbeefcafebabe"
    assert body["conclusion"] == "failure"
    assert body["bypass_label_present"] is False
    assert body["check_run_id"] == 1234567
    assert body["id"]


def test_ci_run_create_is_idempotent_on_same_sha(client: TestClient) -> None:
    """A second POST for the same ``(repo, pr, sha)`` updates in place."""
    r1 = client.post("/ci/runs", json=_payload())
    assert r1.status_code == 201, r1.text
    first_id = r1.json()["id"]

    r2 = client.post(
        "/ci/runs",
        json=_payload(conclusion="success", bypass_label_present=True),
    )
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert body["id"] == first_id, "row id must be preserved on update"
    assert body["conclusion"] == "success"
    assert body["bypass_label_present"] is True


def test_ci_run_lookup_returns_latest(client: TestClient) -> None:
    client.post("/ci/runs", json=_payload(head_sha="aaaaaaaa"))
    client.post("/ci/runs", json=_payload(head_sha="bbbbbbbb", conclusion="success"))
    r = client.get("/ci/runs/acme/users/42")
    assert r.status_code == 200, r.text
    body = r.json()
    # The latest by created_at: the second insert.
    assert body["head_sha"] == "bbbbbbbb"
    assert body["conclusion"] == "success"


def test_ci_run_lookup_404_when_missing(client: TestClient) -> None:
    r = client.get("/ci/runs/acme/users/999")
    assert r.status_code == 404


def test_ci_run_create_rejects_bad_repo_slug(client: TestClient) -> None:
    r = client.post("/ci/runs", json=_payload(repo="not-a-slug"))
    assert r.status_code == 422


def test_ci_run_create_rejects_non_hex_sha(client: TestClient) -> None:
    r = client.post("/ci/runs", json=_payload(head_sha="ZZZZZZZZ"))
    assert r.status_code == 422


def test_ci_run_create_rejects_unknown_conclusion(client: TestClient) -> None:
    r = client.post("/ci/runs", json=_payload(conclusion="explosive"))
    assert r.status_code == 422
