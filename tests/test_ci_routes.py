"""Integration tests for CI routes: upsert and get latest endpoints.

Tests verify the core invariants:
- POST /ci/runs creates and upserts CI run rows
- GET /ci/runs/{owner}/{name}/{pr} returns the latest run
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_post_ci_run_creates_new_record(client: TestClient) -> None:
    """POST /ci/runs creates a new CI run row."""
    payload = {
        "repo": "owner/repo",
        "pr_number": 42,
        "head_sha": "1234567890abcdef",
        "base_sha": "fedcba0987654321",
        "conclusion": "success",
        "report_json": {
            "contract_kind": "openapi",
            "ruleset_id": "test-ruleset",
            "summary": {"breaking": 0, "behavioral": 0, "additive": 1, "total": 1},
            "changes": [],
        },
        "bypass_label_present": False,
    }
    response = client.post("/ci/runs", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert data["repo"] == payload["repo"]
    assert data["pr_number"] == payload["pr_number"]
    assert data["head_sha"] == payload["head_sha"]
    assert data["conclusion"] == payload["conclusion"]
    assert "id" in data
    assert "created_at" in data


def test_post_ci_run_idempotent_on_same_key(client: TestClient) -> None:
    """POST with same (repo, pr, sha) updates existing row; ID preserved."""
    payload1 = {
        "repo": "owner/repo",
        "pr_number": 42,
        "head_sha": "1234567890abcdef",
        "base_sha": "fedcba0987654321",
        "conclusion": "success",
        "report_json": {"summary": {"breaking": 0, "behavioral": 0, "additive": 1, "total": 1}},
    }
    response1 = client.post("/ci/runs", json=payload1)
    assert response1.status_code == 201
    id_1 = response1.json()["id"]

    # Same key, different conclusion
    payload2 = {
        "repo": "owner/repo",
        "pr_number": 42,
        "head_sha": "1234567890abcdef",
        "base_sha": "fedcba0987654321",
        "conclusion": "failure",
        "report_json": {"summary": {"breaking": 1, "behavioral": 0, "additive": 0, "total": 1}},
        "bypass_label_present": True,
    }
    response2 = client.post("/ci/runs", json=payload2)
    assert response2.status_code == 201

    data2 = response2.json()
    assert data2["id"] == id_1  # ID preserved
    assert data2["conclusion"] == "failure"  # Updated
    assert data2["bypass_label_present"] is True  # Updated


def test_post_ci_run_updates_check_run_id(client: TestClient) -> None:
    """POST updates check_run_id when provided."""
    payload1 = {
        "repo": "owner/repo",
        "pr_number": 43,
        "head_sha": "abcdef1234567890",
        "base_sha": "0987654321fedcba",
        "conclusion": "success",
        "check_run_id": 111111,
    }
    response1 = client.post("/ci/runs", json=payload1)
    assert response1.status_code == 201
    assert response1.json()["check_run_id"] == 111111

    # Update with new check_run_id
    payload2 = {
        "repo": "owner/repo",
        "pr_number": 43,
        "head_sha": "abcdef1234567890",
        "base_sha": "0987654321fedcba",
        "conclusion": "success",
        "check_run_id": 222222,
    }
    response2 = client.post("/ci/runs", json=payload2)
    assert response2.status_code == 201
    assert response2.json()["check_run_id"] == 222222


def test_post_ci_run_preserves_check_run_id_when_not_provided(client: TestClient) -> None:
    """POST preserves existing check_run_id when new payload has None."""
    payload1 = {
        "repo": "owner/repo",
        "pr_number": 44,
        "head_sha": "1111111111111111",
        "base_sha": "2222222222222222",
        "conclusion": "success",
        "check_run_id": 333333,
    }
    response1 = client.post("/ci/runs", json=payload1)
    assert response1.status_code == 201

    # Update without check_run_id (None)
    payload2 = {
        "repo": "owner/repo",
        "pr_number": 44,
        "head_sha": "1111111111111111",
        "base_sha": "2222222222222222",
        "conclusion": "failure",
        # check_run_id omitted
    }
    response2 = client.post("/ci/runs", json=payload2)
    assert response2.status_code == 201
    assert response2.json()["check_run_id"] == 333333  # Preserved


def test_distinct_runs_have_different_ids(client: TestClient) -> None:
    """Distinct CI runs get different IDs."""
    payload1 = {
        "repo": "owner1/repo1",
        "pr_number": 1,
        "head_sha": "aaaaaaaaaaaaaaaa",
        "base_sha": "bbbbbbbbbbbbbbbb",
        "conclusion": "success",
    }
    payload2 = {
        "repo": "owner2/repo2",
        "pr_number": 2,
        "head_sha": "cccccccccccccccc",
        "base_sha": "dddddddddddddddd",
        "conclusion": "failure",
    }

    response1 = client.post("/ci/runs", json=payload1)
    response2 = client.post("/ci/runs", json=payload2)

    assert response1.status_code == 201
    assert response2.status_code == 201

    assert response1.json()["id"] != response2.json()["id"]


def test_get_ci_run_returns_404_when_not_found(client: TestClient) -> None:
    """GET /ci/runs/{owner}/{name}/{pr} returns 404 when not found."""
    response = client.get("/ci/runs/nonexistent/repo/999")
    assert response.status_code == 404


def test_get_ci_run_returns_created_run(client: TestClient) -> None:
    """GET returns the created run."""
    payload = {
        "repo": "owner/repo",
        "pr_number": 50,
        "head_sha": "ffffffffffffffff",
        "base_sha": "eeeeeeeeeeeeeeee",
        "conclusion": "success",
    }
    create_response = client.post("/ci/runs", json=payload)
    assert create_response.status_code == 201
    created_id = create_response.json()["id"]

    get_response = client.get("/ci/runs/owner/repo/50")
    assert get_response.status_code == 200

    data = get_response.json()
    assert data["id"] == created_id
    assert data["repo"] == "owner/repo"
    assert data["pr_number"] == 50


def test_get_ci_run_returns_most_recent(client: TestClient) -> None:
    """GET returns the most recently created run when multiple exist."""
    repo = "owner/repo"
    pr_number = 60

    # Create multiple runs for same PR with different head SHAs
    ids = []
    for i in range(3):
        payload = {
            "repo": repo,
            "pr_number": pr_number,
            "head_sha": f"{i:016d}",  # Different SHA each time
            "base_sha": "1111111111111111",
            "conclusion": "success",
        }
        response = client.post("/ci/runs", json=payload)
        assert response.status_code == 201
        ids.append(response.json()["id"])

    # GET should return the most recent (last created)
    get_response = client.get(f"/ci/runs/{repo.split('/')[0]}/{repo.split('/')[1]}/{pr_number}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == ids[-1]


def test_get_ci_run_filters_by_pr_number(client: TestClient) -> None:
    """GET only returns runs for the requested PR number."""
    repo = "owner/repo"

    # Create runs for two different PRs
    payload1 = {
        "repo": repo,
        "pr_number": 70,
        "head_sha": "aaaaaaaaaaaaaaaa",
        "base_sha": "bbbbbbbbbbbbbbbb",
        "conclusion": "success",
    }
    payload2 = {
        "repo": repo,
        "pr_number": 71,
        "head_sha": "cccccccccccccccc",
        "base_sha": "dddddddddddddddd",
        "conclusion": "failure",
    }

    response1 = client.post("/ci/runs", json=payload1)
    response2 = client.post("/ci/runs", json=payload2)
    assert response1.status_code == 201
    assert response2.status_code == 201

    # Get PR 70
    get_response1 = client.get("/ci/runs/owner/repo/70")
    assert get_response1.status_code == 200
    assert get_response1.json()["pr_number"] == 70

    # Get PR 71
    get_response2 = client.get("/ci/runs/owner/repo/71")
    assert get_response2.status_code == 200
    assert get_response2.json()["pr_number"] == 71


def test_get_ci_run_constructs_repo_slug_correctly(client: TestClient) -> None:
    """GET constructs repo slug as owner/name correctly."""
    payload = {
        "repo": "myowner/myrepo",
        "pr_number": 80,
        "head_sha": "1234567890abcdef",
        "base_sha": "fedcba0987654321",
        "conclusion": "success",
    }

    create_response = client.post("/ci/runs", json=payload)
    assert create_response.status_code == 201

    # Access using owner and name separately in URL
    get_response = client.get("/ci/runs/myowner/myrepo/80")
    assert get_response.status_code == 200

    data = get_response.json()
    assert data["repo"] == "myowner/myrepo"
