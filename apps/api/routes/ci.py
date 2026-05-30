"""CI integration routes: persisted GitHub PR check runs.

The Probot app (``apps/github_app``) and the composite GitHub Action
(``action.yml``) call ``POST /ci/runs`` after each PR check to persist
the resulting :class:`~guardian_diff.models.ChangeReport`, the
GitHub-side ``check_run_id``, and the conclusion verdict. ``GET
/ci/runs/{repo}/{pr_number}`` is used by the PR-comment refresher to
fetch the most recent run for a PR (e.g. after a label is added).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from guardian_core.logging import get_logger
from guardian_core.models import CiRun
from guardian_core.schemas import CiRunCreate, CiRunRead
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import get_db

router = APIRouter(prefix="/ci", tags=["ci"])
log = get_logger(__name__)


@router.post(
    "/runs",
    response_model=CiRunRead,
    status_code=status.HTTP_201_CREATED,
)
def upsert_ci_run(payload: CiRunCreate, db: Session = Depends(get_db)) -> CiRunRead:
    """Idempotently upsert a CI run row keyed by ``(repo, pr_number, head_sha)``.

    The Probot app posts here once it has called the Checks API; if the
    same PR head SHA is re-checked (e.g. after the bypass label is
    flipped) the existing row is updated with the new conclusion,
    report, and bypass-label state — the row's ``id`` is preserved so
    downstream references remain stable.
    """
    existing = db.execute(
        select(CiRun)
        .where(CiRun.repo == payload.repo)
        .where(CiRun.pr_number == payload.pr_number)
        .where(CiRun.head_sha == payload.head_sha)
    ).scalar_one_or_none()

    if existing is not None:
        existing.base_sha = payload.base_sha
        existing.conclusion = payload.conclusion
        existing.report_json = payload.report_json
        existing.bypass_label_present = payload.bypass_label_present
        if payload.check_run_id is not None:
            existing.check_run_id = payload.check_run_id
        try:
            db.commit()
        except IntegrityError as exc:  # pragma: no cover - guard against race
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="ci_run conflict",
            ) from exc
        db.refresh(existing)
        log.info(
            "ci.runs.updated",
            repo=payload.repo,
            pr=payload.pr_number,
            sha=payload.head_sha,
            conclusion=payload.conclusion,
            bypass=payload.bypass_label_present,
        )
        return CiRunRead.model_validate(existing)

    row = CiRun(
        repo=payload.repo,
        pr_number=payload.pr_number,
        head_sha=payload.head_sha,
        base_sha=payload.base_sha,
        conclusion=payload.conclusion,
        report_json=payload.report_json,
        bypass_label_present=payload.bypass_label_present,
        check_run_id=payload.check_run_id,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:  # pragma: no cover - guarded by select above
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ci_run conflict",
        ) from exc
    db.refresh(row)
    log.info(
        "ci.runs.created",
        repo=payload.repo,
        pr=payload.pr_number,
        sha=payload.head_sha,
        conclusion=payload.conclusion,
        bypass=payload.bypass_label_present,
    )
    return CiRunRead.model_validate(row)


@router.get(
    "/runs/{repo_owner}/{repo_name}/{pr_number}",
    response_model=CiRunRead,
)
def get_latest_ci_run(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    db: Session = Depends(get_db),
) -> CiRunRead:
    """Return the most recent ``ci_runs`` row for a given PR."""
    repo = f"{repo_owner}/{repo_name}"
    row = db.execute(
        select(CiRun)
        .where(CiRun.repo == repo)
        .where(CiRun.pr_number == pr_number)
        .order_by(CiRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ci_run not found",
        )
    return CiRunRead.model_validate(row)
