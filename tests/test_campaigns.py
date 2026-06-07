"""Tests for the deprecation campaign orchestrator.

Covers:
* CampaignFSM state transitions and guards
* EWMA decay computation
* Campaign CRUD + inline evaluation via the API
* ``GET /campaigns/{id}`` decay curve and remaining clients
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from guardian_campaigns.decay import compute_ewma
from guardian_campaigns.state_machine import CampaignFSM

# ---------------------------------------------------------------------------
# State machine unit tests
# ---------------------------------------------------------------------------


class TestCampaignFSM:
    """Verify state transitions and guard logic in isolation."""

    def test_initial_state_is_draft(self) -> None:
        fsm = CampaignFSM("c1", "draft", peak_usage=0)
        assert fsm.current_state == "draft"

    def test_activate_draft_to_active(self) -> None:
        fsm = CampaignFSM("c1", "draft", peak_usage=0)
        fsm.activate(peak_usage=1000)  # type: ignore[attr-defined]
        assert fsm.current_state == "active"
        assert fsm.peak_usage == 1000

    def test_activate_sets_peak_usage(self) -> None:
        fsm = CampaignFSM("c1", "draft", peak_usage=0)
        fsm.activate(peak_usage=500)  # type: ignore[attr-defined]
        assert fsm.peak_usage == 500

    def test_start_decay_fires_when_usage_below_threshold(self) -> None:
        """Usage at 4% of peak should cross the 5% threshold → decaying."""
        fsm = CampaignFSM("c1", "active", peak_usage=1000, usage_threshold_pct=5.0)
        fired = fsm.evaluate(current_usage=40)  # 4%
        assert fired == "start_decay"
        assert fsm.current_state == "decaying"

    def test_start_decay_does_not_fire_when_usage_above_threshold(self) -> None:
        fsm = CampaignFSM("c1", "active", peak_usage=1000, usage_threshold_pct=5.0)
        fired = fsm.evaluate(current_usage=100)  # 10%
        assert fired is None
        assert fsm.current_state == "active"

    def test_mark_ready_fires_when_usage_near_zero(self) -> None:
        """Usage at 0 should trigger decaying → ready_to_remove."""
        fsm = CampaignFSM("c1", "decaying", peak_usage=1000, usage_threshold_pct=5.0)
        fired = fsm.evaluate(current_usage=0)
        assert fired == "mark_ready"
        assert fsm.current_state == "ready_to_remove"

    def test_mark_ready_does_not_fire_when_usage_above_1pct(self) -> None:
        fsm = CampaignFSM("c1", "decaying", peak_usage=1000, usage_threshold_pct=5.0)
        fired = fsm.evaluate(current_usage=15)  # 1.5%
        assert fired is None
        assert fsm.current_state == "decaying"

    def test_manual_complete(self) -> None:
        fsm = CampaignFSM("c1", "ready_to_remove", peak_usage=1000)
        fsm.complete()  # type: ignore[attr-defined]
        assert fsm.current_state == "completed"

    def test_abort_from_active(self) -> None:
        fsm = CampaignFSM("c1", "active", peak_usage=1000)
        fsm.abort()  # type: ignore[attr-defined]
        assert fsm.current_state == "aborted"

    def test_abort_from_decaying(self) -> None:
        fsm = CampaignFSM("c1", "decaying", peak_usage=1000)
        fsm.abort()  # type: ignore[attr-defined]
        assert fsm.current_state == "aborted"

    def test_abort_from_draft(self) -> None:
        fsm = CampaignFSM("c1", "draft", peak_usage=0)
        fsm.abort()  # type: ignore[attr-defined]
        assert fsm.current_state == "aborted"

    def test_no_transition_from_completed(self) -> None:
        fsm = CampaignFSM("c1", "completed", peak_usage=0)
        # evaluate should not raise even though no triggers are valid.
        fired = fsm.evaluate(0)
        assert fired is None
        assert fsm.current_state == "completed"

    def test_evaluate_zero_peak_start_decay(self) -> None:
        """Zero peak_usage means threshold is always exceeded → start_decay."""
        fsm = CampaignFSM("c1", "active", peak_usage=0, usage_threshold_pct=5.0)
        fired = fsm.evaluate(current_usage=0)
        assert fired == "start_decay"

    def test_custom_threshold(self) -> None:
        """High threshold (50%) should trigger decay at 40% usage."""
        fsm = CampaignFSM("c1", "active", peak_usage=100, usage_threshold_pct=50.0)
        fired = fsm.evaluate(current_usage=40)
        assert fired == "start_decay"

    def test_custom_threshold_not_triggered(self) -> None:
        """With 50% threshold, 60% usage should NOT trigger decay."""
        fsm = CampaignFSM("c1", "active", peak_usage=100, usage_threshold_pct=50.0)
        fired = fsm.evaluate(current_usage=60)
        assert fired is None


# ---------------------------------------------------------------------------
# EWMA decay tests
# ---------------------------------------------------------------------------


class TestEWMADecay:
    def test_alpha_computation(self) -> None:
        from guardian_campaigns.decay import _alpha

        # span=29 → α = 2/30 ≈ 0.0667
        a = _alpha(29)
        assert abs(a - 2 / 30) < 1e-10

    def test_ewma_converges_toward_new_value(self) -> None:
        """Repeated sampling with the same value should converge to that value."""
        ewma = 1000.0
        for _ in range(200):
            ewma = compute_ewma(0.0, ewma, decay_window_days=30)
        assert ewma < 1.0  # converged to near 0

    def test_ewma_stays_at_value_when_constant(self) -> None:
        """If all samples equal the initial EWMA the value should not change."""
        initial = 500.0
        result = compute_ewma(500.0, initial, decay_window_days=30)
        assert abs(result - 500.0) < 1e-6

    def test_ewma_increases_for_rising_usage(self) -> None:
        ewma = 100.0
        result = compute_ewma(500.0, ewma, decay_window_days=30)
        assert result > ewma


# ---------------------------------------------------------------------------
# API integration tests (using the TestClient + migrated SQLite DB)
# ---------------------------------------------------------------------------


class TestCampaignAPI:
    def test_create_campaign(self, client: TestClient) -> None:
        resp = client.post(
            "/campaigns",
            json={"name": "Deprecate /old-endpoint", "usage_threshold_pct": 10.0},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["state"] == "draft"
        assert data["name"] == "Deprecate /old-endpoint"
        assert data["usage_threshold_pct"] == 10.0
        assert data["decay_curve"] == []
        assert data["remaining_clients"] == []

    def test_get_campaign_returns_full_payload(self, client: TestClient) -> None:
        resp = client.post("/campaigns", json={"name": "Test campaign"})
        assert resp.status_code == 201
        cid = resp.json()["id"]

        resp2 = client.get(f"/campaigns/{cid}")
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["id"] == cid
        assert "decay_curve" in data
        assert "remaining_clients" in data
        assert "reminder_prs" in data

    def test_get_campaign_404(self, client: TestClient) -> None:
        resp = client.get("/campaigns/nonexistent-id")
        assert resp.status_code == 404

    def test_transition_draft_to_active(self, client: TestClient) -> None:
        resp = client.post("/campaigns", json={"name": "Activate me"})
        cid = resp.json()["id"]
        resp2 = client.post(
            f"/campaigns/{cid}/transition",
            json={"trigger": "activate", "peak_usage": 500},
        )
        assert resp2.status_code == 200
        assert resp2.json()["state"] == "active"

    def test_transition_active_to_aborted(self, client: TestClient) -> None:
        resp = client.post("/campaigns", json={"name": "Abort me"})
        cid = resp.json()["id"]
        client.post(
            f"/campaigns/{cid}/transition",
            json={"trigger": "activate", "peak_usage": 100},
        )
        resp2 = client.post(
            f"/campaigns/{cid}/transition",
            json={"trigger": "abort"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["state"] == "aborted"

    def test_update_campaign(self, client: TestClient) -> None:
        resp = client.post("/campaigns", json={"name": "Updateable"})
        cid = resp.json()["id"]
        resp2 = client.patch(
            f"/campaigns/{cid}",
            json={"description": "Updated description", "usage_threshold_pct": 20.0},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["description"] == "Updated description"
        assert data["usage_threshold_pct"] == 20.0

    def test_evaluate_inline_no_endpoint(self, client: TestClient) -> None:
        """Evaluate on a campaign with no endpoint produces a metric row with 0 usage."""
        resp = client.post("/campaigns", json={"name": "No-endpoint campaign"})
        cid = resp.json()["id"]
        # Activate first so state isn't terminal.
        client.post(
            f"/campaigns/{cid}/transition",
            json={"trigger": "activate", "peak_usage": 0},
        )
        resp2 = client.post(f"/campaigns/{cid}/evaluate")
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["campaign_id"] == cid
        assert data["metric"] is not None
        assert data["metric"]["usage_count"] == 0

    def test_evaluate_inline_transitions_to_decaying(self, client: TestClient) -> None:
        """Active campaign with 0 peak_usage evaluates to decaying immediately."""
        resp = client.post(
            "/campaigns",
            json={"name": "Fast decay", "usage_threshold_pct": 5.0},
        )
        cid = resp.json()["id"]
        client.post(
            f"/campaigns/{cid}/transition",
            json={"trigger": "activate", "peak_usage": 0},
        )
        resp2 = client.post(f"/campaigns/{cid}/evaluate")
        assert resp2.status_code == 200
        data = resp2.json()
        # peak_usage=0, so guard passes immediately
        assert data["new_state"] == "decaying"

    def test_get_campaign_decay_curve_populated_after_evaluate(self, client: TestClient) -> None:
        resp = client.post("/campaigns", json={"name": "Curve test"})
        cid = resp.json()["id"]
        client.post(
            f"/campaigns/{cid}/transition",
            json={"trigger": "activate", "peak_usage": 0},
        )
        client.post(f"/campaigns/{cid}/evaluate")
        resp2 = client.get(f"/campaigns/{cid}")
        data = resp2.json()
        assert len(data["decay_curve"]) >= 1
        point = data["decay_curve"][0]
        assert "sampled_at" in point
        assert "ewma_value" in point
        assert "usage_count" in point
        assert "remaining_client_count" in point

    def test_full_lifecycle_draft_to_completed(self, client: TestClient) -> None:
        """Walk the full FSM path: draft→active→decaying→ready_to_remove→completed."""
        resp = client.post(
            "/campaigns",
            json={"name": "Full lifecycle", "usage_threshold_pct": 5.0},
        )
        cid = resp.json()["id"]
        assert resp.json()["state"] == "draft"

        client.post(f"/campaigns/{cid}/transition", json={"trigger": "activate", "peak_usage": 0})
        assert client.get(f"/campaigns/{cid}").json()["state"] == "active"

        # Evaluate: peak=0 → start_decay fires → decaying
        client.post(f"/campaigns/{cid}/evaluate")
        assert client.get(f"/campaigns/{cid}").json()["state"] == "decaying"

        # Evaluate again: still 0 usage → mark_ready fires → ready_to_remove
        client.post(f"/campaigns/{cid}/evaluate")
        assert client.get(f"/campaigns/{cid}").json()["state"] == "ready_to_remove"

        client.post(f"/campaigns/{cid}/transition", json={"trigger": "complete"})
        assert client.get(f"/campaigns/{cid}").json()["state"] == "completed"
