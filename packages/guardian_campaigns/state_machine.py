"""Campaign state machine backed by the ``transitions`` 0.9 library.

States
------
draft          → campaign created, not yet active
active         → deprecation announced, usage being monitored
decaying       → usage fell below threshold; reminder PRs being sent
ready_to_remove → usage is near-zero; safe to remove the deprecated item
completed      → endpoint/field has been removed
aborted        → campaign cancelled at any pre-completed state

Transitions
-----------
activate        draft           → active         (sets peak_usage)
start_decay     active          → decaying        (usage < threshold)
mark_ready      decaying        → ready_to_remove (usage ≈ 0)
complete        ready_to_remove → completed       (manual)
abort           draft/active/decaying/ready_to_remove → aborted

Guards
------
``start_decay``: rolling-window usage < usage_threshold_pct% of peak_usage.
``mark_ready``:  rolling-window usage < 1% of peak_usage  (or peak == 0).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from guardian_core.logging import get_logger
from transitions import Machine, MachineError

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

# Minimum seconds between automatic transitions on the same FSM instance.
# Prevents tight-loop re-evaluation when guard conditions oscillate near
# the threshold boundary (e.g. EWMA floating-point noise).
_TRANSITION_LOCK_SECONDS: float = 5.0

# Epsilon subtracted from thresholds before floating-point comparison so
# that values exactly on the boundary do not oscillate between states.
_GUARD_EPSILON: float = 0.01

STATES: list[str] = [
    "draft",
    "active",
    "decaying",
    "ready_to_remove",
    "completed",
    "aborted",
]

_TRANSITIONS: list[dict[str, Any]] = [
    {
        "trigger": "activate",
        "source": "draft",
        "dest": "active",
        "before": "_before_activate",
    },
    {
        "trigger": "start_decay",
        "source": "active",
        "dest": "decaying",
        "conditions": "_guard_start_decay",
    },
    {
        "trigger": "mark_ready",
        "source": "decaying",
        "dest": "ready_to_remove",
        "conditions": "_guard_mark_ready",
    },
    {
        "trigger": "complete",
        "source": "ready_to_remove",
        "dest": "completed",
    },
    {
        "trigger": "abort",
        "source": ["draft", "active", "decaying", "ready_to_remove"],
        "dest": "aborted",
    },
]


class CampaignFSM:
    """Finite-state machine for a single deprecation campaign.

    Instances are ephemeral — created from the persisted ``Campaign`` row,
    driven by the evaluation job, and the resulting ``state`` string written
    back to the database. No ORM models are imported here to keep the FSM
    testable in isolation.

    Parameters
    ----------
    campaign_id:
        The campaign's PK string.
    initial_state:
        The current persisted state to resume from.
    peak_usage:
        Peak rolling-window usage, used by decay guards.
    usage_threshold_pct:
        Usage must drop below this percentage of ``peak_usage`` to trigger
        ``start_decay``.
    """

    def __init__(
        self,
        campaign_id: str,
        initial_state: str,
        peak_usage: int,
        usage_threshold_pct: float = 5.0,
    ) -> None:
        self.campaign_id = campaign_id
        self.peak_usage = peak_usage
        self.usage_threshold_pct = usage_threshold_pct
        # Set by evaluate(); used by guards.
        self._current_usage: int = 0
        # Monotonic timestamp of the last automatic transition; prevents
        # rapid re-evaluation within the same FSM instance from firing
        # multiple transitions in quick succession.
        self._last_transition_at: float | None = None

        machine = Machine(
            model=self,
            states=STATES,
            transitions=_TRANSITIONS,
            initial=initial_state,
            auto_transitions=False,
            send_event=False,
        )
        # Keep a reference so mypy sees the attribute.
        self._machine = machine

    # ------------------------------------------------------------------
    # Guard callbacks
    # ------------------------------------------------------------------

    def _guard_start_decay(self) -> bool:
        """Return True when usage has decayed below the configured threshold.

        Uses an epsilon subtraction (``_GUARD_EPSILON``) so that values on the
        exact boundary don't oscillate between states due to floating-point
        noise in the EWMA computation.
        """
        if self.peak_usage <= 0:
            return True
        ratio = self._current_usage / self.peak_usage * 100.0
        ok = ratio <= self.usage_threshold_pct - _GUARD_EPSILON
        log.debug(
            "campaign.guard.start_decay",
            campaign_id=self.campaign_id,
            usage=self._current_usage,
            peak=self.peak_usage,
            ratio_pct=round(ratio, 2),
            threshold_pct=self.usage_threshold_pct,
            effective_threshold=self.usage_threshold_pct - _GUARD_EPSILON,
            passes=ok,
        )
        return ok

    def _guard_mark_ready(self) -> bool:
        """Return True when usage is essentially zero (≤ 0.99% of peak).

        Uses an epsilon subtraction so the boundary (exactly 1%) is treated
        as above-threshold, preventing oscillation.
        """
        if self.peak_usage <= 0:
            return True
        ratio = self._current_usage / self.peak_usage * 100.0
        ok = ratio <= 1.0 - _GUARD_EPSILON
        log.debug(
            "campaign.guard.mark_ready",
            campaign_id=self.campaign_id,
            usage=self._current_usage,
            peak=self.peak_usage,
            ratio_pct=round(ratio, 2),
            effective_threshold=1.0 - _GUARD_EPSILON,
            passes=ok,
        )
        return ok

    # ------------------------------------------------------------------
    # Before callbacks
    # ------------------------------------------------------------------

    def _before_activate(self, peak_usage: int = 0) -> None:
        """Record the peak usage at activation time."""
        self.peak_usage = peak_usage
        log.info(
            "campaign.activate",
            campaign_id=self.campaign_id,
            peak_usage=peak_usage,
        )

    # ------------------------------------------------------------------
    # Evaluation helpers
    # ------------------------------------------------------------------

    def evaluate(self, current_usage: int) -> str | None:
        """Drive automatic transitions based on *current_usage*.

        Returns the name of the trigger that fired, or ``None`` if no
        automatic transition occurred.  Callers (jobs) are responsible
        for persisting the resulting ``self.state`` back to the database.

        Re-entrant safety: if a transition was already fired on this FSM
        instance within ``_TRANSITION_LOCK_SECONDS`` the method returns
        ``None`` immediately to prevent oscillation from rapid repeated
        calls (e.g. two workers picking up the same evaluation job).
        """
        now = time.monotonic()
        if (
            self._last_transition_at is not None
            and now - self._last_transition_at < _TRANSITION_LOCK_SECONDS
        ):
            log.debug(
                "campaign.evaluate.locked",
                campaign_id=self.campaign_id,
                elapsed=round(now - self._last_transition_at, 3),
            )
            return None

        self._current_usage = current_usage
        prev: str = str(getattr(self, "state", ""))

        fired: str | None = None
        # Attempt automatic transitions in order.
        for trigger in ("start_decay", "mark_ready"):
            # Check if we're in the right source state and the trigger exists.
            try:
                result = getattr(self, trigger)()
            except MachineError:
                # Trigger not valid from the current source state — a real
                # bug in a guard/callback now surfaces instead of being
                # swallowed. Try the next trigger in order.
                continue
            if result:
                fired = trigger
                break

        if fired:
            self._last_transition_at = time.monotonic()
            log.info(
                "campaign.transition",
                campaign_id=self.campaign_id,
                trigger=fired,
                from_state=prev,
                to_state=str(getattr(self, "state", "")),
            )
        return fired

    @property
    def current_state(self) -> str:
        """Return the current state string."""
        return str(getattr(self, "state", ""))
