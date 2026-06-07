"""RQ queue and rq-scheduler setup for campaign background jobs.

One queue per priority level:

* ``campaigns-high``    – evaluate_campaign (state checks)
* ``campaigns-default`` – send_reminder_pr
* ``campaigns-low``     – housekeeping / bulk re-evaluations

All queues are backed by the Guardian's shared Redis instance
(``REDIS_URL`` env var).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from guardian_core.logging import get_logger
from guardian_core.redis_client import get_redis_url

log = get_logger(__name__)

_QUEUE_NAMES = {
    "high": "campaigns-high",
    "default": "campaigns-default",
    "low": "campaigns-low",
}


def _make_redis_connection() -> Any:
    """Return a redis.Redis connection suitable for RQ."""
    import redis

    url = get_redis_url()
    return redis.Redis.from_url(url, socket_connect_timeout=2.0, socket_timeout=5.0)


def get_queue(priority: str = "default") -> Any:
    """Return an RQ ``Queue`` for the given *priority* level.

    The connection is created fresh each call so workers and web
    processes each own their socket.
    """
    from rq import Queue

    name = _QUEUE_NAMES.get(priority, _QUEUE_NAMES["default"])
    conn = _make_redis_connection()
    return Queue(name, connection=conn)


def enqueue_evaluate(campaign_id: str, *, priority: str = "high") -> Any:
    """Enqueue an ``evaluate_campaign`` job.

    Returns the RQ ``Job`` object (or ``None`` when Redis is unavailable).
    """
    from guardian_campaigns.jobs import evaluate_campaign

    try:
        q = get_queue(priority)
        job = q.enqueue(
            evaluate_campaign,
            campaign_id,
            job_id=f"eval-{campaign_id}",
            job_timeout=120,
        )
        log.info(
            "campaign.scheduler.enqueue_evaluate",
            campaign_id=campaign_id,
            job_id=job.id,
        )
        return job
    except Exception as exc:
        log.warning(
            "campaign.scheduler.enqueue_evaluate.failed",
            campaign_id=campaign_id,
            error=str(exc),
        )
        return None


def schedule_reminder_pr(
    campaign_id: str,
    client_repo: str,
    *,
    delay_seconds: int = 0,
    priority: str = "default",
) -> Any:
    """Schedule a ``send_reminder_pr`` job, optionally delayed.

    When *delay_seconds* > 0 the job is scheduled via ``rq-scheduler``
    (which requires the ``rqscheduler`` process to be running).  Otherwise
    it is enqueued immediately.
    """
    from guardian_campaigns.jobs import send_reminder_pr

    try:
        if delay_seconds > 0:
            from rq_scheduler import Scheduler

            conn = _make_redis_connection()
            scheduler = Scheduler(
                queue_name=_QUEUE_NAMES.get(priority, _QUEUE_NAMES["default"]),
                connection=conn,
            )
            job = scheduler.enqueue_in(
                timedelta(seconds=delay_seconds),
                send_reminder_pr,
                campaign_id,
                client_repo,
                job_id=f"reminder-{campaign_id}-{client_repo.replace('/', '_')}",
                timeout=300,
            )
        else:
            q = get_queue(priority)
            job = q.enqueue(
                send_reminder_pr,
                campaign_id,
                client_repo,
                job_id=f"reminder-{campaign_id}-{client_repo.replace('/', '_')}",
                job_timeout=300,
            )
        log.info(
            "campaign.scheduler.schedule_reminder",
            campaign_id=campaign_id,
            repo=client_repo,
            delay_seconds=delay_seconds,
        )
        return job
    except Exception as exc:
        log.warning(
            "campaign.scheduler.schedule_reminder.failed",
            campaign_id=campaign_id,
            repo=client_repo,
            error=str(exc),
        )
        return None
