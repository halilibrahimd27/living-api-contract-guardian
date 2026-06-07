"""Campaign orchestrator API routes.

Endpoints
---------
POST   /campaigns                  – create a new deprecation campaign
GET    /campaigns/{id}             – fetch campaign with decay curve + remaining clients
PATCH  /campaigns/{id}            – update mutable campaign fields
POST   /campaigns/{id}/transition  – fire a state-machine trigger
POST   /campaigns/{id}/evaluate    – run the evaluation job inline (dev/test)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast, get_args

import structlog
from fastapi import APIRouter, Depends, HTTPException
from guardian_campaigns.schemas import (
    CampaignCreate,
    CampaignRead,
    CampaignState,
    CampaignTransitionRequest,
    CampaignUpdate,
    EvaluateResult,
    MetricPoint,
    ReminderPRRead,
)
from guardian_core.models import Campaign, CampaignMetric, ReminderPR
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

_VALID_STATES: frozenset[str] = frozenset(get_args(CampaignState))


def _coerce_state(state: str) -> CampaignState:
    """Cast DB state string to the ``CampaignState`` literal type."""
    if state in _VALID_STATES:
        return cast(CampaignState, state)
    return "draft"


def _read_campaign(session: Session, campaign_id: str) -> Campaign:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def _build_read(campaign: Campaign, session: Session) -> CampaignRead:
    """Assemble the full ``CampaignRead`` response with decay curve and clients."""
    metrics_rows = (
        session.execute(
            select(CampaignMetric)
            .where(CampaignMetric.campaign_id == campaign.id)
            .order_by(CampaignMetric.sampled_at.asc())
        )
        .scalars()
        .all()
    )
    decay_curve = [
        MetricPoint(
            sampled_at=m.sampled_at,
            usage_count=m.usage_count,
            ewma_value=m.ewma_value,
            remaining_client_count=m.remaining_client_count,
        )
        for m in metrics_rows
    ]

    # Remaining clients: repos whose most recent ReminderPR is still open/pending.
    pr_rows_open = (
        session.execute(
            select(ReminderPR).where(
                ReminderPR.campaign_id == campaign.id,
                ReminderPR.pr_state.in_(["pending", "open"]),
            )
        )
        .scalars()
        .all()
    )
    remaining_clients = [pr.client_repo for pr in pr_rows_open]

    all_pr_rows = (
        session.execute(select(ReminderPR).where(ReminderPR.campaign_id == campaign.id))
        .scalars()
        .all()
    )
    reminder_pr_reads = [
        ReminderPRRead(
            id=pr.id,
            client_repo=pr.client_repo,
            pr_number=pr.pr_number,
            branch_name=pr.branch_name,
            pr_state=pr.pr_state,
        )
        for pr in all_pr_rows
    ]

    return CampaignRead(
        id=campaign.id,
        name=campaign.name,
        description=campaign.description,
        endpoint_id=campaign.endpoint_id,
        field_path=campaign.field_path,
        state=_coerce_state(campaign.state),
        usage_threshold_pct=campaign.usage_threshold_pct,
        decay_window_days=campaign.decay_window_days,
        peak_usage=campaign.peak_usage,
        github_repo=campaign.github_repo,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        decay_curve=decay_curve,
        remaining_clients=remaining_clients,
        reminder_prs=reminder_pr_reads,
    )


@router.get("", response_model=list[CampaignRead])
def list_campaigns(
    session: Session = Depends(get_db),
) -> list[CampaignRead]:
    """List all deprecation campaigns ordered by creation date."""
    campaigns = (
        session.execute(select(Campaign).order_by(Campaign.created_at.desc())).scalars().all()
    )
    return [_build_read(c, session) for c in campaigns]


@router.post("", response_model=CampaignRead, status_code=201)
def create_campaign(
    body: CampaignCreate,
    session: Session = Depends(get_db),
) -> CampaignRead:
    """Create a new deprecation campaign in ``draft`` state."""
    campaign = Campaign(
        name=body.name,
        description=body.description,
        endpoint_id=body.endpoint_id,
        field_path=body.field_path,
        usage_threshold_pct=body.usage_threshold_pct,
        decay_window_days=body.decay_window_days,
        github_repo=body.github_repo,
        state="draft",
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    log.info("campaign.created", campaign_id=campaign.id, name=campaign.name)
    return _build_read(campaign, session)


@router.get("/{campaign_id}", response_model=CampaignRead)
def get_campaign(
    campaign_id: str,
    session: Session = Depends(get_db),
) -> CampaignRead:
    """Fetch a campaign with its full decay curve and remaining clients."""
    campaign = _read_campaign(session, campaign_id)
    return _build_read(campaign, session)


@router.patch("/{campaign_id}", response_model=CampaignRead)
def update_campaign(
    campaign_id: str,
    body: CampaignUpdate,
    session: Session = Depends(get_db),
) -> CampaignRead:
    """Update mutable fields on a campaign."""
    campaign = _read_campaign(session, campaign_id)
    if body.description is not None:
        campaign.description = body.description
    if body.usage_threshold_pct is not None:
        campaign.usage_threshold_pct = body.usage_threshold_pct
    if body.decay_window_days is not None:
        campaign.decay_window_days = body.decay_window_days
    if body.github_repo is not None:
        campaign.github_repo = body.github_repo
    campaign.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(campaign)
    return _build_read(campaign, session)


@router.post("/{campaign_id}/transition", response_model=CampaignRead)
def transition_campaign(
    campaign_id: str,
    body: CampaignTransitionRequest,
    session: Session = Depends(get_db),
) -> CampaignRead:
    """Fire a manual state-machine trigger on the campaign."""
    from guardian_campaigns.state_machine import CampaignFSM

    campaign = _read_campaign(session, campaign_id)

    fsm = CampaignFSM(
        campaign_id=campaign.id,
        initial_state=campaign.state,
        peak_usage=campaign.peak_usage,
        usage_threshold_pct=campaign.usage_threshold_pct,
    )

    if body.trigger == "activate" and body.peak_usage is not None:
        try:
            fsm.activate(peak_usage=body.peak_usage)  # type: ignore[attr-defined]
            campaign.peak_usage = body.peak_usage
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        trigger_fn = getattr(fsm, body.trigger, None)
        if trigger_fn is None:
            raise HTTPException(status_code=422, detail=f"Unknown trigger: {body.trigger}")
        try:
            trigger_fn()
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    campaign.state = fsm.current_state
    campaign.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(campaign)
    log.info(
        "campaign.transition.manual",
        campaign_id=campaign_id,
        trigger=body.trigger,
        new_state=campaign.state,
    )
    return _build_read(campaign, session)


@router.post("/{campaign_id}/evaluate", response_model=EvaluateResult)
def evaluate_campaign_inline(
    campaign_id: str,
    session: Session = Depends(get_db),
) -> EvaluateResult:
    """Run the campaign evaluation job inline (useful for dev / tests).

    For production use, enqueue via the RQ scheduler instead.
    """
    from guardian_campaigns.decay import get_rolling_usage, record_metric
    from guardian_campaigns.state_machine import CampaignFSM

    campaign = _read_campaign(session, campaign_id)

    if campaign.state in ("completed", "aborted"):
        cs = _coerce_state(campaign.state)
        return EvaluateResult(
            campaign_id=campaign_id,
            previous_state=cs,
            new_state=cs,
            transition_fired=None,
            metric=None,
            extra={"skipped": True, "reason": "terminal_state"},
        )

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

    session.commit()

    mp = MetricPoint(
        sampled_at=metric.sampled_at,
        usage_count=metric.usage_count,
        ewma_value=metric.ewma_value,
        remaining_client_count=metric.remaining_client_count,
    )
    return EvaluateResult(
        campaign_id=campaign_id,
        previous_state=_coerce_state(prev_state),
        new_state=_coerce_state(campaign.state),
        transition_fired=fired,
        metric=mp,
    )
