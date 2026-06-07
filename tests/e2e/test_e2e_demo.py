"""End-to-end demo: seed fixtures, run a diff, exercise campaigns.

All tests require a live API (gated by the ``require_api`` session fixture in
conftest.py).  Run the full stack first::

    docker compose -f infra/docker-compose.e2e.yml up -d

Then::

    pytest tests/e2e/ -m e2e -v

The tests do NOT use Playwright — dashboard pages are checked with plain HTTP
GETs to verify they return 200s.
"""

from __future__ import annotations

import time
from typing import cast

import httpx
import pytest
import structlog

log = structlog.get_logger(__name__)

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Minimal OpenAPI specs used as fixtures
# ---------------------------------------------------------------------------

_SPEC_V1: dict[str, object] = {
    "openapi": "3.0.0",
    "info": {"title": "Demo API", "version": "1.0.0"},
    "paths": {
        "/users": {
            "get": {
                "operationId": "listUsers",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/users/{id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}

# v2 removes /users/{id} — a breaking change
_SPEC_V2: dict[str, object] = {
    "openapi": "3.0.0",
    "info": {"title": "Demo API", "version": "2.0.0"},
    "paths": {
        "/users": {
            "get": {
                "operationId": "listUsers",
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}


# ---------------------------------------------------------------------------
# 1. Health check
# ---------------------------------------------------------------------------


def test_healthz(http: httpx.Client) -> None:
    """API health endpoint should return db_ok=true."""
    resp = http.get("/healthz")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["db_ok"] is True
    log.info("e2e.healthz.ok", version=payload.get("version"))


# ---------------------------------------------------------------------------
# 2. Seed fixtures: service + contract
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_service(http: httpx.Client) -> dict[str, object]:
    """Create (or re-use) an e2e demo service."""
    resp = http.post(
        "/services",
        json={"name": "e2e-demo-service", "owner": "e2e-team"},
    )
    # 201 created or 409 already exists — either is fine
    if resp.status_code == 409:
        # Fetch by re-creating; the real API has no list endpoint.
        # We rely on the fact that the name is unique.
        log.info("e2e.service.already_exists")
        # Create with a unique suffix to avoid flakiness
        ts = str(int(time.time()))
        resp = http.post(
            "/services",
            json={"name": f"e2e-demo-service-{ts}", "owner": "e2e-team"},
        )
    assert resp.status_code == 201, f"Unexpected status {resp.status_code}: {resp.text}"
    service: dict[str, object] = cast(dict[str, object], resp.json())
    log.info("e2e.service.created", service_id=service["id"])
    return service


@pytest.fixture(scope="module")
def demo_contract_v1(http: httpx.Client, demo_service: dict[str, object]) -> dict[str, object]:
    """Upload the v1 contract for the demo service."""
    service_id: str = str(demo_service["id"])
    resp = http.post(
        f"/services/{service_id}/contracts",
        json={
            "name": "demo-openapi",
            "kind": "openapi",
            "spec": _SPEC_V1,
        },
    )
    assert resp.status_code == 201, f"Unexpected status {resp.status_code}: {resp.text}"
    contract: dict[str, object] = cast(dict[str, object], resp.json())
    log.info(
        "e2e.contract.v1.created",
        contract_id=contract["id"],
        version_hash=cast(dict[str, object], contract["version"])["version_hash"],
    )
    return contract


@pytest.fixture(scope="module")
def demo_contract_v2(http: httpx.Client, demo_service: dict[str, object]) -> dict[str, object]:
    """Upload the v2 contract for the demo service."""
    service_id: str = str(demo_service["id"])
    resp = http.post(
        f"/services/{service_id}/contracts",
        json={
            "name": "demo-openapi-v2",
            "kind": "openapi",
            "spec": _SPEC_V2,
        },
    )
    assert resp.status_code == 201, f"Unexpected status {resp.status_code}: {resp.text}"
    contract: dict[str, object] = cast(dict[str, object], resp.json())
    log.info(
        "e2e.contract.v2.created",
        contract_id=contract["id"],
        version_hash=cast(dict[str, object], contract["version"])["version_hash"],
    )
    return contract


def test_service_created(demo_service: dict[str, object]) -> None:
    assert demo_service["id"]
    assert demo_service["name"]
    assert demo_service["owner"] == "e2e-team"


def test_contract_v1_uploaded(demo_contract_v1: dict[str, object]) -> None:
    assert demo_contract_v1["id"]
    assert demo_contract_v1["kind"] == "openapi"
    version = demo_contract_v1["version"]
    assert isinstance(version, dict)
    assert version["version_hash"]


def test_contract_v2_uploaded(demo_contract_v2: dict[str, object]) -> None:
    assert demo_contract_v2["id"]
    assert demo_contract_v2["kind"] == "openapi"


# ---------------------------------------------------------------------------
# 3. Diff: simulate a breaking-change PR
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_diff(http: httpx.Client) -> dict[str, object]:
    """POST /diff and assert we get breaking changes back."""
    resp = http.post(
        "/diff",
        json={
            "kind": "openapi",
            "before_spec": _SPEC_V1,
            "after_spec": _SPEC_V2,
        },
    )
    assert resp.status_code == 200, f"Diff failed: {resp.text}"
    report: dict[str, object] = cast(dict[str, object], resp.json())
    summary = cast(dict[str, object], report["summary"])
    log.info(
        "e2e.diff.produced",
        diff_id=report.get("diff_id"),
        breaking=summary["breaking"],
    )
    return report


def test_diff_has_breaking_changes(demo_diff: dict[str, object]) -> None:
    """Removing /users/{id} must be flagged as breaking."""
    summary = demo_diff["summary"]
    assert isinstance(summary, dict)
    assert (
        summary["breaking"] >= 1
    ), f"Expected at least 1 breaking change, got {summary['breaking']}"


def test_diff_summary_totals(demo_diff: dict[str, object]) -> None:
    summary = demo_diff["summary"]
    assert isinstance(summary, dict)
    changes = demo_diff["changes"]
    assert isinstance(changes, list)
    assert summary["total"] == len(changes)


def test_diff_change_records_have_required_fields(demo_diff: dict[str, object]) -> None:
    changes = demo_diff["changes"]
    assert isinstance(changes, list)
    assert len(changes) > 0
    for change in changes:
        assert "change_id" in change
        assert "verdict" in change
        assert change["verdict"] in ("additive", "behavioral", "breaking")
        assert "rule_id" in change
        assert "location" in change


# ---------------------------------------------------------------------------
# 4. Campaigns: create, transition state machine
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_campaign(http: httpx.Client) -> dict[str, object]:
    """Create a draft campaign."""
    ts = str(int(time.time()))
    resp = http.post(
        "/campaigns",
        json={
            "name": f"e2e-deprecate-users-id-{ts}",
            "description": "Deprecate GET /users/{id} endpoint (e2e demo)",
            "usage_threshold_pct": 5.0,
            "decay_window_days": 30,
        },
    )
    assert resp.status_code == 201, f"Campaign creation failed: {resp.text}"
    campaign: dict[str, object] = cast(dict[str, object], resp.json())
    log.info("e2e.campaign.created", campaign_id=campaign["id"], state=campaign["state"])
    return campaign


def test_campaign_created_in_draft(demo_campaign: dict[str, object]) -> None:
    assert demo_campaign["state"] == "draft"
    assert demo_campaign["id"]
    assert demo_campaign["decay_curve"] == []
    assert demo_campaign["remaining_clients"] == []


def test_campaign_activate_transition(http: httpx.Client, demo_campaign: dict[str, object]) -> None:
    """Activate the campaign with a known peak_usage."""
    campaign_id: str = str(demo_campaign["id"])
    resp = http.post(
        f"/campaigns/{campaign_id}/transition",
        json={"trigger": "activate", "peak_usage": 100},
    )
    assert resp.status_code == 200, f"Transition failed: {resp.text}"
    campaign = resp.json()
    assert campaign["state"] == "active", f"Expected active, got {campaign['state']}"
    assert campaign["peak_usage"] == 100
    log.info("e2e.campaign.activated", campaign_id=campaign_id)


def test_campaign_evaluate_inline(http: httpx.Client, demo_campaign: dict[str, object]) -> None:
    """Run the inline evaluator and check we get a metric back."""
    campaign_id: str = str(demo_campaign["id"])
    resp = http.post(f"/campaigns/{campaign_id}/evaluate")
    assert resp.status_code == 200, f"Evaluate failed: {resp.text}"
    result = resp.json()
    assert result["campaign_id"] == campaign_id
    assert result["new_state"] in (
        "active",
        "decaying",
        "ready_to_remove",
        "completed",
        "aborted",
    )
    log.info(
        "e2e.campaign.evaluated",
        campaign_id=campaign_id,
        previous_state=result["previous_state"],
        new_state=result["new_state"],
        transition_fired=result["transition_fired"],
    )


def test_campaign_get_returns_decay_curve(
    http: httpx.Client, demo_campaign: dict[str, object]
) -> None:
    """After evaluate, GET /campaigns/{id} should include a decay curve."""
    campaign_id: str = str(demo_campaign["id"])
    resp = http.get(f"/campaigns/{campaign_id}")
    assert resp.status_code == 200, f"Get campaign failed: {resp.text}"
    campaign = resp.json()
    assert isinstance(campaign["decay_curve"], list)
    # We called evaluate once, so there should be at least 1 metric point.
    assert len(campaign["decay_curve"]) >= 1
    first_point = campaign["decay_curve"][0]
    assert "usage_count" in first_point
    assert "ewma_value" in first_point
    assert "remaining_client_count" in first_point


# ---------------------------------------------------------------------------
# 5. Dashboard HTTP smoke tests (no Playwright, just verify 200 responses)
# ---------------------------------------------------------------------------


def _dashboard_reachable(dashboard_url: str) -> bool:
    try:
        resp = httpx.get(dashboard_url, timeout=3.0)
        return resp.status_code < 500
    except Exception:
        return False


def test_dashboard_home_page(dashboard_url: str) -> None:
    """Dashboard home page should return 200."""
    if not _dashboard_reachable(dashboard_url):
        pytest.skip(f"Dashboard not reachable at {dashboard_url}")
    resp = httpx.get(dashboard_url, timeout=10.0)
    assert resp.status_code == 200, f"Home page failed: {resp.status_code}"


def test_dashboard_campaigns_page(dashboard_url: str) -> None:
    """Campaigns list page should return 200."""
    if not _dashboard_reachable(dashboard_url):
        pytest.skip(f"Dashboard not reachable at {dashboard_url}")
    resp = httpx.get(f"{dashboard_url}/campaigns", timeout=10.0)
    assert resp.status_code == 200, f"Campaigns page failed: {resp.status_code}"


def test_dashboard_campaign_detail_page(
    dashboard_url: str, demo_campaign: dict[str, object]
) -> None:
    """Campaign detail page for the demo campaign should return 200."""
    if not _dashboard_reachable(dashboard_url):
        pytest.skip(f"Dashboard not reachable at {dashboard_url}")
    campaign_id: str = str(demo_campaign["id"])
    resp = httpx.get(f"{dashboard_url}/campaigns/{campaign_id}", timeout=10.0)
    assert resp.status_code == 200, f"Campaign detail page failed: {resp.status_code}"
