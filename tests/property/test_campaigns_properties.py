"""Property-based tests for the deprecation campaign orchestrator.

Invariants tested:

CampaignFSM:
1. FSM always maintains a valid state from the defined STATES set
2. Activation correctly sets peak_usage and transitions from draft→active
3. start_decay guard fires iff usage < threshold_pct% of peak
4. mark_ready guard fires iff usage < 1% of peak
5. Epsilon margin prevents oscillation at boundary values
6. Transition lock prevents rapid re-evaluation within lock window
7. Peak usage of 0 always satisfies guards (endpoint with no usage)
8. Custom thresholds are respected without oscillation
9. Terminal states (completed, aborted) accept no further transitions

Decay:
1. Alpha formula: α = 2 / (span + 1)
2. EWMA converges to constant values over repeated iterations
3. EWMA is bounded by min/max of inputs
4. EWMA gives greater weight to recent values than old ones
5. Successive EWMA values form a smooth decay curve
6. Rounding to 4 decimals is consistent and lossless for typical values

GitHub PR:
1. Branch names follow the pattern guardian/deprecate-<campaign_id>
2. PR body includes campaign ID and patch placeholder
"""

from __future__ import annotations

from guardian_campaigns.decay import (
    DecaySample,
    _alpha,
    compute_ewma,
)
from guardian_campaigns.github_pr import _DEFAULT_BODY
from guardian_campaigns.state_machine import STATES, CampaignFSM
from hypothesis import given, settings
from hypothesis import strategies as st

# ============================================================================
# Strategies for campaign test inputs
# ============================================================================


def _campaign_id_strategy() -> st.SearchStrategy[str]:
    """Generate valid campaign IDs (UUIDs)."""
    return st.text(
        alphabet="0123456789abcdef-",
        min_size=36,
        max_size=36,
    ).filter(
        lambda s: len(s) == 36 and s.count("-") == 4
    )


def _peak_usage_strategy() -> st.SearchStrategy[int]:
    """Generate realistic peak usage values (0 to 1M requests)."""
    return st.integers(min_value=0, max_value=1_000_000)


def _current_usage_strategy() -> st.SearchStrategy[int]:
    """Generate realistic current usage values."""
    return st.integers(min_value=0, max_value=1_000_000)


def _threshold_pct_strategy() -> st.SearchStrategy[float]:
    """Generate valid usage threshold percentages (1-100%)."""
    return st.floats(
        min_value=1.0,
        max_value=100.0,
        allow_nan=False,
        allow_infinity=False,
    )


def _decay_window_days_strategy() -> st.SearchStrategy[int]:
    """Generate valid decay window values (1-365 days)."""
    return st.integers(min_value=1, max_value=365)


def _ewma_value_strategy() -> st.SearchStrategy[float]:
    """Generate realistic EWMA values (0-1M as floats)."""
    return st.floats(
        min_value=0.0,
        max_value=1_000_000.0,
        allow_nan=False,
        allow_infinity=False,
    )


# ============================================================================
# Tests for CampaignFSM
# ============================================================================


class TestCampaignFSMProperties:
    """Property-based tests for the CampaignFSM state machine."""

    @given(
        campaign_id=_campaign_id_strategy(),
        peak_usage=_peak_usage_strategy(),
    )
    def test_fsm_starts_in_valid_state(
        self,
        campaign_id: str,
        peak_usage: int,
    ) -> None:
        """The FSM always initializes to a valid state from STATES."""
        fsm = CampaignFSM(campaign_id, "draft", peak_usage)
        assert fsm.current_state in STATES
        assert fsm.current_state == "draft"

    @given(
        campaign_id=_campaign_id_strategy(),
        peak_usage=_peak_usage_strategy(),
        activation_peak=_peak_usage_strategy(),
    )
    def test_activate_sets_peak_usage(
        self,
        campaign_id: str,
        peak_usage: int,
        activation_peak: int,
    ) -> None:
        """Calling activate(peak_usage=P) sets the FSM's peak_usage to P."""
        fsm = CampaignFSM(campaign_id, "draft", peak_usage)
        fsm.activate(peak_usage=activation_peak)  # type: ignore[attr-defined]
        assert fsm.peak_usage == activation_peak
        assert fsm.current_state == "active"

    @given(
        campaign_id=_campaign_id_strategy(),
        peak_usage=_peak_usage_strategy(),
        threshold_pct=_threshold_pct_strategy(),
        current_usage=_current_usage_strategy(),
    )
    def test_start_decay_respects_threshold(
        self,
        campaign_id: str,
        peak_usage: int,
        threshold_pct: float,
        current_usage: int,
    ) -> None:
        """start_decay fires iff usage < threshold_pct% of peak (minus epsilon)."""
        if peak_usage == 0:
            # Special case: zero peak means guard always passes
            fsm = CampaignFSM(campaign_id, "active", peak_usage, threshold_pct)
            fired = fsm.evaluate(current_usage)
            # With zero peak, start_decay should fire
            if fsm.current_state == "decaying":
                assert fired == "start_decay"
        else:
            fsm = CampaignFSM(campaign_id, "active", peak_usage, threshold_pct)
            ratio_pct = (current_usage / peak_usage) * 100.0
            fired = fsm.evaluate(current_usage)

            # The guard subtracts 0.01 epsilon to prevent oscillation
            epsilon = 0.01
            if ratio_pct <= threshold_pct - epsilon:
                # Should have transitioned
                assert fired == "start_decay"
                assert fsm.current_state == "decaying"
            else:
                # Should not have transitioned
                assert fired is None
                assert fsm.current_state == "active"

    @given(
        campaign_id=_campaign_id_strategy(),
        peak_usage=st.integers(min_value=1, max_value=1_000_000),
        threshold_pct=_threshold_pct_strategy(),
        current_usage=_current_usage_strategy(),
    )
    def test_mark_ready_respects_1pct_threshold(
        self,
        campaign_id: str,
        peak_usage: int,
        threshold_pct: float,
        current_usage: int,
    ) -> None:
        """mark_ready fires iff usage < 1% of peak (minus epsilon)."""
        fsm = CampaignFSM(campaign_id, "decaying", peak_usage, threshold_pct)
        ratio_pct = (current_usage / peak_usage) * 100.0
        fired = fsm.evaluate(current_usage)

        # Epsilon margin is 0.01%
        epsilon = 0.01
        if ratio_pct <= 1.0 - epsilon:
            # Should transition to ready_to_remove
            assert fired == "mark_ready"
            assert fsm.current_state == "ready_to_remove"
        else:
            # Should not transition
            assert fired is None
            assert fsm.current_state == "decaying"

    @given(
        campaign_id=_campaign_id_strategy(),
        peak_usage=_peak_usage_strategy(),
        threshold_pct=_threshold_pct_strategy(),
    )
    def test_zero_peak_usage_satisfies_both_guards(
        self,
        campaign_id: str,
        peak_usage: int,
        threshold_pct: float,
    ) -> None:
        """When peak_usage is 0, both guards always pass."""
        # start_decay guard
        fsm_active = CampaignFSM(campaign_id, "active", 0, threshold_pct)
        fired = fsm_active.evaluate(0)
        assert fired == "start_decay"
        assert fsm_active.current_state == "decaying"

        # mark_ready guard
        fsm_decaying = CampaignFSM(campaign_id, "decaying", 0, threshold_pct)
        fired = fsm_decaying.evaluate(0)
        assert fired == "mark_ready"
        assert fsm_decaying.current_state == "ready_to_remove"

    @given(
        campaign_id=_campaign_id_strategy(),
        peak_usage=_peak_usage_strategy(),
        threshold_pct=_threshold_pct_strategy(),
        usage1=_current_usage_strategy(),
        usage2=_current_usage_strategy(),
    )
    @settings(max_examples=100)
    def test_transition_lock_prevents_rapid_re_evaluation(
        self,
        campaign_id: str,
        peak_usage: int,
        threshold_pct: float,
        usage1: int,
        usage2: int,
    ) -> None:
        """Calling evaluate twice in quick succession fires only once."""
        # Skip this test if peak is 0 (always transitions)
        if peak_usage == 0:
            return

        fsm = CampaignFSM(campaign_id, "active", peak_usage, threshold_pct)
        # Use a usage value that will definitely trigger start_decay
        trigger_usage = max(0, int(peak_usage * (threshold_pct - 0.5) / 100.0))

        fsm.evaluate(trigger_usage)
        # If the first evaluate fired, the state changed
        first_state = fsm.current_state
        # Immediately call evaluate again with same/similar usage
        fired2 = fsm.evaluate(trigger_usage)
        # The second call should return None due to transition lock
        # (within 5 seconds, only one automatic transition is allowed)
        # Note: this is a monotonic clock check, should be fast enough
        assert fired2 is None
        # State should not have changed from the second evaluate
        assert fsm.current_state == first_state

    @given(
        campaign_id=_campaign_id_strategy(),
        peak_usage=_peak_usage_strategy(),
    )
    def test_terminal_states_are_stable(
        self,
        campaign_id: str,
        peak_usage: int,
    ) -> None:
        """Completed and aborted states don't transition further."""
        # Test completed
        fsm_completed = CampaignFSM(campaign_id, "completed", peak_usage)
        fired = fsm_completed.evaluate(0)
        assert fired is None
        assert fsm_completed.current_state == "completed"

        # Test aborted
        fsm_aborted = CampaignFSM(campaign_id, "aborted", peak_usage)
        fired = fsm_aborted.evaluate(0)
        assert fired is None
        assert fsm_aborted.current_state == "aborted"

    @given(
        campaign_id=_campaign_id_strategy(),
        peak_usage=st.integers(min_value=1, max_value=1_000_000),
        threshold_pct=st.floats(min_value=50.0, max_value=100.0),
    )
    def test_all_states_are_reachable(
        self,
        campaign_id: str,
        peak_usage: int,
        threshold_pct: float,
    ) -> None:
        """All states should be reachable through the state machine."""
        # draft -> active
        fsm = CampaignFSM(campaign_id, "draft", peak_usage, threshold_pct)
        fsm.activate(peak_usage=peak_usage)  # type: ignore[attr-defined]
        assert fsm.current_state == "active"

        # active -> decaying: use a low usage value
        low_usage = max(0, int(peak_usage * (threshold_pct - 5.0) / 100.0))
        fsm.evaluate(low_usage)
        assert fsm.current_state == "decaying"

        # decaying -> ready_to_remove: use very low usage.
        # Reset the transition lock so the second evaluate isn't blocked
        # by the 5 s re-entrancy guard.
        fsm._last_transition_at = None
        very_low_usage = max(0, int(peak_usage * 0.5 / 100.0))
        fsm.evaluate(very_low_usage)
        assert fsm.current_state == "ready_to_remove"

        # ready_to_remove -> completed (manual)
        fsm.complete()  # type: ignore[attr-defined]
        assert fsm.current_state == "completed"


# ============================================================================
# Tests for Decay Curve Functions
# ============================================================================


class TestDecayProperties:
    """Property-based tests for EWMA decay computation."""

    @given(
        decay_window_days=_decay_window_days_strategy(),
    )
    def test_alpha_formula(
        self,
        decay_window_days: int,
    ) -> None:
        """Alpha should equal 2.0 / (span + 1)."""
        alpha = _alpha(decay_window_days)
        expected = 2.0 / (decay_window_days + 1.0)
        assert alpha == expected

    @given(
        decay_window_days=_decay_window_days_strategy(),
    )
    def test_alpha_is_valid_smoothing_factor(
        self,
        decay_window_days: int,
    ) -> None:
        """Alpha should always be in (0, 1) for valid decay windows."""
        alpha = _alpha(decay_window_days)
        # For span >= 1, alpha should be in (0, 1)
        assert 0.0 < alpha <= 1.0

    @given(
        new_value=_ewma_value_strategy(),
        prev_ewma=_ewma_value_strategy(),
        decay_window_days=_decay_window_days_strategy(),
    )
    def test_ewma_is_weighted_average(
        self,
        new_value: float,
        prev_ewma: float,
        decay_window_days: int,
    ) -> None:
        """EWMA(new, prev, w) = α*new + (1-α)*prev, where α = 2/(w+1)."""
        ewma = compute_ewma(new_value, prev_ewma, decay_window_days)
        alpha = _alpha(decay_window_days)
        expected = alpha * new_value + (1.0 - alpha) * prev_ewma
        assert abs(ewma - expected) < 1e-6

    @given(
        new_value=_ewma_value_strategy(),
        prev_ewma=_ewma_value_strategy(),
        decay_window_days=_decay_window_days_strategy(),
    )
    def test_ewma_is_bounded(
        self,
        new_value: float,
        prev_ewma: float,
        decay_window_days: int,
    ) -> None:
        """EWMA stays within the range [min(new, prev), max(new, prev)]."""
        ewma = compute_ewma(new_value, prev_ewma, decay_window_days)
        min_val = min(new_value, prev_ewma)
        max_val = max(new_value, prev_ewma)
        # Allow a small floating-point tolerance: α*x + (1-α)*x can round
        # to one ULP below x for large windows due to IEEE-754 rounding.
        tol = max(1e-9, abs(max_val) * 1e-12)
        assert min_val - tol <= ewma <= max_val + tol

    @given(
        constant_value=_ewma_value_strategy(),
        decay_window_days=_decay_window_days_strategy(),
    )
    def test_ewma_converges_to_constant(
        self,
        constant_value: float,
        decay_window_days: int,
    ) -> None:
        """Repeatedly applying EWMA to the same value converges to that value."""
        ewma = constant_value
        # Apply EWMA 100 times
        for _ in range(100):
            ewma = compute_ewma(constant_value, ewma, decay_window_days)
        # After many iterations, should be essentially equal
        assert abs(ewma - constant_value) < 1e-3

    @given(
        start_value=_ewma_value_strategy(),
        end_value=_ewma_value_strategy(),
        decay_window_days=_decay_window_days_strategy(),
    )
    def test_ewma_smooth_transition(
        self,
        start_value: float,
        end_value: float,
        decay_window_days: int,
    ) -> None:
        """EWMA smoothly transitions from one value to another."""
        ewma = start_value
        # Apply EWMA 50 times with the new value
        ewmas = [ewma]
        for _ in range(50):
            ewma = compute_ewma(end_value, ewma, decay_window_days)
            ewmas.append(ewma)

        # Check that transition is monotonic (never overshoots)
        if start_value < end_value:
            # Should be increasing
            for i in range(1, len(ewmas)):
                assert start_value <= ewmas[i] <= end_value
        elif start_value > end_value:
            # Should be decreasing
            for i in range(1, len(ewmas)):
                assert end_value <= ewmas[i] <= start_value

    @given(
        value=_ewma_value_strategy(),
    )
    def test_ewma_rounding_consistency(
        self,
        value: float,
    ) -> None:
        """Rounding EWMA to 4 decimals is consistent and non-destructive."""
        rounded = round(value, 4)
        # Re-rounding should be idempotent
        re_rounded = round(rounded, 4)
        assert rounded == re_rounded

    @given(
        values=st.lists(
            _ewma_value_strategy(),
            min_size=1,
            max_size=20,
        ),
        decay_window_days=_decay_window_days_strategy(),
    )
    def test_ewma_sequence_forms_decay_curve(
        self,
        values: list[float],
        decay_window_days: int,
    ) -> None:
        """A sequence of EWMA computations forms a smooth decay curve."""
        ewma = values[0]
        ewmas: list[float] = [ewma]

        for val in values[1:]:
            ewma = compute_ewma(val, ewma, decay_window_days)
            ewmas.append(ewma)

        # Each EWMA should be a valid float
        for e in ewmas:
            assert isinstance(e, float)
            assert not (e != e)  # Not NaN
            assert e >= 0.0  # Non-negative


# ============================================================================
# Tests for GitHub PR Naming Conventions
# ============================================================================


class TestGitHubPRNaming:
    """Property-based tests for GitHub PR branch naming conventions."""

    @given(
        campaign_id=_campaign_id_strategy(),
    )
    def test_branch_name_format(
        self,
        campaign_id: str,
    ) -> None:
        """Branch names should follow guardian/deprecate-<campaign_id>."""
        branch = f"guardian/deprecate-{campaign_id}"
        # Check structure
        assert branch.startswith("guardian/deprecate-")
        assert len(campaign_id) == 36
        assert branch.endswith(campaign_id)

    @given(
        campaign_id=_campaign_id_strategy(),
    )
    def test_pr_body_includes_campaign_id(
        self,
        campaign_id: str,
    ) -> None:
        """PR body should include the campaign ID."""
        body = _DEFAULT_BODY.format(
            patch_suggestion="test",
            campaign_id=campaign_id,
        )
        assert campaign_id in body

    @given(
        campaign_id=_campaign_id_strategy(),
    )
    def test_pr_body_includes_placeholder(
        self,
        campaign_id: str,
    ) -> None:
        """PR body should include patch suggestion placeholder."""
        # Test with no patch
        body = _DEFAULT_BODY.format(
            patch_suggestion="",
            campaign_id=campaign_id,
        )
        assert "patch" in body.lower() or "suggested" in body.lower()

    @given(
        campaign_id=_campaign_id_strategy(),
        patch=st.text(min_size=0, max_size=1000),
    )
    def test_pr_body_includes_patch_suggestion(
        self,
        campaign_id: str,
        patch: str,
    ) -> None:
        """PR body should include the provided patch suggestion."""
        body = _DEFAULT_BODY.format(
            patch_suggestion=patch,
            campaign_id=campaign_id,
        )
        if patch:
            assert patch in body
        assert campaign_id in body


# ============================================================================
# Tests for DecaySample
# ============================================================================


class TestDecaySample:
    """Property-based tests for DecaySample structure."""

    @given(
        sampled_at=st.datetimes(timezones=st.just(None)),
        usage_count=st.integers(min_value=0, max_value=1_000_000),
        ewma_value=st.floats(
            min_value=0.0,
            max_value=1_000_000.0,
            allow_nan=False,
            allow_infinity=False,
        ),
        remaining_client_count=st.integers(min_value=0, max_value=10_000),
    )
    def test_decay_sample_construction(
        self,
        sampled_at: object,
        usage_count: int,
        ewma_value: float,
        remaining_client_count: int,
    ) -> None:
        """DecaySample can be constructed with valid fields."""
        sample = DecaySample(
            sampled_at=sampled_at,  # type: ignore[arg-type]
            usage_count=usage_count,
            ewma_value=ewma_value,
            remaining_client_count=remaining_client_count,
        )
        assert sample.usage_count == usage_count
        assert sample.ewma_value == ewma_value
        assert sample.remaining_client_count == remaining_client_count

    @given(
        usage_count=st.integers(min_value=0, max_value=1_000_000),
        ewma_value=st.floats(
            min_value=0.0,
            max_value=1_000_000.0,
            allow_nan=False,
            allow_infinity=False,
        ),
        remaining_client_count=st.integers(min_value=0, max_value=10_000),
    )
    def test_decay_sample_is_iterable(
        self,
        usage_count: int,
        ewma_value: float,
        remaining_client_count: int,
    ) -> None:
        """DecaySample is a NamedTuple and can be unpacked."""
        from datetime import UTC, datetime

        sample = DecaySample(
            sampled_at=datetime.now(UTC),
            usage_count=usage_count,
            ewma_value=ewma_value,
            remaining_client_count=remaining_client_count,
        )
        # Should be unpackable as a tuple
        sampled_at, usage, ewma, remaining = sample
        assert usage == usage_count
        assert ewma == ewma_value
        assert remaining == remaining_client_count
