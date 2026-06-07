"""EWMA-based decay curve computation for deprecation campaigns.

The Exponentially Weighted Moving Average smooths noisy daily usage
readings so that the state-machine guards respond to trends rather than
momentary spikes.

``alpha`` is derived from the campaign's ``decay_window_days`` using the
common "span" convention:  α = 2 / (span + 1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from guardian_core.logging import get_logger
from guardian_core.models import CampaignMetric
from sqlalchemy import func, select
from sqlalchemy.orm import Session

log = get_logger(__name__)


class DecaySample(NamedTuple):
    """One raw data point for the decay curve."""

    sampled_at: datetime
    usage_count: int
    ewma_value: float
    remaining_client_count: int


def _alpha(decay_window_days: int) -> float:
    """Compute the EWMA smoothing factor from the window span."""
    span = max(1, decay_window_days)
    return 2.0 / (span + 1.0)


def compute_ewma(
    new_value: float,
    prev_ewma: float,
    decay_window_days: int,
) -> float:
    """Return the next EWMA given *new_value* and the *prev_ewma*."""
    a = _alpha(decay_window_days)
    return a * new_value + (1.0 - a) * prev_ewma


def get_rolling_usage(
    session: Session,
    endpoint_id: str,
    window_days: int,
) -> tuple[int, int]:
    """Return ``(total_usage, distinct_client_count)`` over the rolling window.

    Queries the ``usages`` table for all rows whose ``window_end`` falls
    within the last *window_days* days.  Returns zeros when no endpoint_id
    is provided or there are no rows.
    """
    from guardian_core.models import Usage  # local to avoid circular imports

    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    rows = session.execute(
        select(
            func.coalesce(func.sum(Usage.request_count), 0),
            func.count(func.distinct(Usage.client_id)),
        ).where(
            Usage.endpoint_id == endpoint_id,
            Usage.window_end >= cutoff,
        )
    ).one()
    total: int = int(rows[0])
    clients: int = int(rows[1])
    return total, clients


def record_metric(
    session: Session,
    campaign_id: str,
    usage_count: int,
    remaining_client_count: int,
    decay_window_days: int,
) -> CampaignMetric:
    """Compute EWMA and persist a new ``CampaignMetric`` row.

    The previous EWMA is read from the latest existing metric row
    (or zero on the first sample).  The new row is added to *session*
    but **not** committed — callers own the transaction.
    """
    from guardian_core.models import CampaignMetric as CampaignMetricModel

    prev_row = (
        session.execute(
            select(CampaignMetricModel)
            .where(CampaignMetricModel.campaign_id == campaign_id)
            .order_by(CampaignMetricModel.sampled_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    prev_ewma = prev_row.ewma_value if prev_row is not None else float(usage_count)

    ewma = compute_ewma(float(usage_count), prev_ewma, decay_window_days)
    now = datetime.now(UTC)

    metric = CampaignMetricModel(
        campaign_id=campaign_id,
        sampled_at=now,
        usage_count=usage_count,
        ewma_value=round(ewma, 4),
        remaining_client_count=remaining_client_count,
    )
    session.add(metric)
    log.debug(
        "campaign.metric.recorded",
        campaign_id=campaign_id,
        usage_count=usage_count,
        ewma=round(ewma, 4),
        remaining_client_count=remaining_client_count,
    )
    return metric
