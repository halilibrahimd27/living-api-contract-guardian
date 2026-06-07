"""Re-entrant RQ job functions for the campaign orchestrator.

All jobs use ``SELECT ... FOR UPDATE SKIP LOCKED`` when claiming
exclusive work so that multiple workers can run in parallel without
double-processing.  SQLite (used in tests) silently ignores
``FOR UPDATE SKIP LOCKED`` but the logic is otherwise identical.

Jobs
----
evaluate_campaign   – sample usage, update EWMA, drive state transitions,
                      schedule reminder PRs when entering decaying state.
send_reminder_pr    – open a GitHub reminder PR for one client repo.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TypeVar

from guardian_core.db import session_scope
from guardian_core.logging import get_logger
from guardian_core.models import Campaign, ReminderPR
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from guardian_campaigns.decay import get_rolling_usage, record_metric
from guardian_campaigns.state_machine import CampaignFSM

log = get_logger(__name__)

_T = TypeVar("_T")


def _maybe_for_update(stmt: Select[tuple[_T]], session: Session) -> Select[tuple[_T]]:
    """Apply FOR UPDATE SKIP LOCKED on PostgreSQL; no-op on SQLite."""
    bind = session.get_bind()
    dialect = getattr(bind, "dialect", None)
    name = getattr(dialect, "name", "") if dialect else ""
    if name == "postgresql":
        return stmt.with_for_update(skip_locked=True)
    return stmt


def evaluate_campaign(campaign_id: str) -> dict[str, object]:
    """Sample usage, update EWMA, and drive state transitions.

    Re-entrant: acquires a row-level lock via FOR UPDATE SKIP LOCKED so
    concurrent workers skip already-locked campaigns.

    Returns a summary dict suitable for logging / RQ job result.
    """
    with session_scope() as session:
        stmt = _maybe_for_update(
            select(Campaign).where(Campaign.id == campaign_id),
            session,
        )
        campaign = session.execute(stmt).scalars().first()

        if campaign is None:
            log.warning("campaign.evaluate.not_found", campaign_id=campaign_id)
            return {"campaign_id": campaign_id, "skipped": True, "reason": "not_found"}

        if campaign.state in ("completed", "aborted"):
            log.debug(
                "campaign.evaluate.terminal",
                campaign_id=campaign_id,
                state=campaign.state,
            )
            return {
                "campaign_id": campaign_id,
                "skipped": True,
                "reason": "terminal_state",
            }

        # --- Usage sampling ---
        usage_count = 0
        remaining_clients = 0
        if campaign.endpoint_id:
            usage_count, remaining_clients = get_rolling_usage(
                session, campaign.endpoint_id, campaign.decay_window_days
            )

        metric = record_metric(
            session,
            campaign_id=campaign_id,
            usage_count=usage_count,
            remaining_client_count=remaining_clients,
            decay_window_days=campaign.decay_window_days,
        )

        # --- State machine ---
        prev_state = campaign.state
        fsm = CampaignFSM(
            campaign_id=campaign_id,
            initial_state=campaign.state,
            peak_usage=campaign.peak_usage,
            usage_threshold_pct=campaign.usage_threshold_pct,
        )
        fired = fsm.evaluate(usage_count)

        if fired:
            campaign.state = fsm.current_state
            campaign.updated_at = datetime.now(UTC)

        result: dict[str, object] = {
            "campaign_id": campaign_id,
            "previous_state": prev_state,
            "new_state": campaign.state,
            "transition_fired": fired,
            "usage_count": usage_count,
            "remaining_clients": remaining_clients,
            "ewma": metric.ewma_value,
        }

        # --- Schedule reminder PRs when entering/in decaying state ---
        if campaign.state == "decaying" and campaign.github_repo:
            _maybe_schedule_reminder(session, campaign)

        log.info("campaign.evaluate.done", **{k: str(v) for k, v in result.items()})
        return result


def _maybe_schedule_reminder(session: Session, campaign: Campaign) -> None:
    """Queue a reminder PR job if not already pending/open for this repo."""
    from guardian_campaigns.scheduler import schedule_reminder_pr

    if not campaign.github_repo:
        return

    existing = (
        session.execute(
            select(ReminderPR).where(
                ReminderPR.campaign_id == campaign.id,
                ReminderPR.client_repo == campaign.github_repo,
                ReminderPR.pr_state.in_(["pending", "open"]),
            )
        )
        .scalars()
        .first()
    )
    if existing:
        log.debug(
            "campaign.reminder.already_tracked",
            campaign_id=campaign.id,
            repo=campaign.github_repo,
            pr_state=existing.pr_state,
        )
        return

    branch = f"guardian/deprecate-{campaign.id}"
    pr_row = ReminderPR(
        campaign_id=campaign.id,
        client_repo=campaign.github_repo,
        branch_name=branch,
        pr_state="pending",
    )
    session.add(pr_row)
    # Commit the row *before* enqueuing so the send_reminder_pr worker can
    # never observe an uncommitted ReminderPR (nor collide with this row on
    # the unique (campaign_id, client_repo) key if it races ahead). If the
    # enqueue below fails, the pending row is reconciled on the next evaluate.
    session.commit()

    schedule_reminder_pr(campaign.id, campaign.github_repo, delay_seconds=0)


def send_reminder_pr(campaign_id: str, client_repo: str) -> dict[str, object]:
    """Open (or confirm existence of) a GitHub reminder PR.

    Re-entrant: skips if an open PR already exists for the branch, and
    updates the ``reminder_prs`` row with the final PR number + state.
    """
    with session_scope() as session:
        stmt = _maybe_for_update(
            select(ReminderPR).where(
                ReminderPR.campaign_id == campaign_id,
                ReminderPR.client_repo == client_repo,
            ),
            session,
        )
        pr_row = session.execute(stmt).scalars().first()

        if pr_row is None:
            # Row may not exist yet in a retry scenario; create a placeholder.
            branch = f"guardian/deprecate-{campaign_id}"
            pr_row = ReminderPR(
                campaign_id=campaign_id,
                client_repo=client_repo,
                branch_name=branch,
                pr_state="pending",
            )
            session.add(pr_row)
            session.flush()

        if pr_row.pr_state in ("open", "merged", "closed"):
            log.debug(
                "campaign.reminder_pr.already_done",
                campaign_id=campaign_id,
                repo=client_repo,
                pr_state=pr_row.pr_state,
            )
            return {
                "campaign_id": campaign_id,
                "repo": client_repo,
                "skipped": True,
                "pr_number": pr_row.pr_number,
            }

        # Build patch suggestion from guide if available.
        patch = _build_patch_suggestion(session, campaign_id)

        from guardian_campaigns.github_pr import open_reminder_pr

        github_token = os.environ.get("GITHUB_TOKEN", "")
        if not github_token:
            log.warning(
                "campaign.reminder_pr.no_token",
                campaign_id=campaign_id,
                repo=client_repo,
            )
            return {
                "campaign_id": campaign_id,
                "repo": client_repo,
                "skipped": True,
                "reason": "no_github_token",
            }

        result = open_reminder_pr(
            campaign_id=campaign_id,
            client_repo=client_repo,
            patch_suggestion=patch,
            github_token=github_token,
        )

        pr_row.pr_number = result["pr_number"]
        pr_row.pr_state = "open"
        pr_row.updated_at = datetime.now(UTC)

        log.info(
            "campaign.reminder_pr.done",
            campaign_id=campaign_id,
            repo=client_repo,
            pr_number=result["pr_number"],
            created=result["created"],
        )
        return {
            "campaign_id": campaign_id,
            "repo": client_repo,
            "pr_number": result["pr_number"],
            "created": result["created"],
            "branch": result["branch"],
        }


def _build_patch_suggestion(session: Session, campaign_id: str) -> str:
    """Return a patch suggestion string for the reminder PR body.

    Looks for unambiguous single-hunk code snippets in the most recent
    guide associated with the campaign's ``ContractDiff``.  Falls back
    to a generic message when no guides are available.
    """
    import re

    from guardian_core.models import Campaign as CampaignModel
    from guardian_core.models import Guide

    campaign = session.get(CampaignModel, campaign_id)
    if campaign is None or campaign.endpoint_id is None:
        return ""

    # Find the most recent guide row for any diff.
    guide = (
        session.execute(select(Guide).order_by(Guide.created_at.desc()).limit(1)).scalars().first()
    )
    if guide is None:
        return ""

    # Extract code fences from the guide markdown.
    blocks = re.findall(r"```[^\n]*\n(.*?)```", guide.markdown, re.DOTALL)
    if not blocks:
        return ""

    # Return the first code block as the patch suggestion.
    return f"```\n{blocks[0].strip()}\n```"
