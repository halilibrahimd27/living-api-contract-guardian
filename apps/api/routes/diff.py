"""Diff route: ``POST /diff`` classifies two contracts as additive / behavioral / breaking."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, status
from guardian_core.logging import get_logger
from guardian_core.models import ContractDiff
from guardian_core.schemas import DiffRequest
from guardian_diff import ChangeReport, RuleSet, diff_contracts, load_default_rules
from guardian_diff.ruleset import load_rules_from_text
from sqlalchemy.orm import Session

from apps.api.deps import get_db

router = APIRouter(prefix="/diff", tags=["diff"])
log = get_logger(__name__)


def _build_ruleset(payload: DiffRequest) -> RuleSet:
    defaults = load_default_rules()
    if payload.rules_yaml is None:
        return defaults
    try:
        custom = load_rules_from_text(payload.rules_yaml, source="POST /diff body")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid rules_yaml: {exc}",
        ) from exc
    return defaults.merge(custom)


@router.post(
    "",
    response_model=ChangeReport,
    status_code=status.HTTP_200_OK,
)
def diff(
    payload: DiffRequest,
    db: Session = Depends(get_db),
) -> ChangeReport:
    """Compute a structured diff between two contract versions."""
    ruleset = _build_ruleset(payload)

    if payload.kind == "openapi":
        if payload.before_spec is None or payload.after_spec is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="openapi diff requires 'before_spec' and 'after_spec'",
            )
        report = diff_contracts(
            kind="openapi",
            before=payload.before_spec,
            after=payload.after_spec,
            ruleset=ruleset,
            session=db,
            spectral=payload.run_spectral,
        )
    else:  # proto
        if payload.before_b64 is None or payload.after_b64 is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="proto diff requires 'before_b64' and 'after_b64'",
            )
        try:
            before_bytes = base64.b64decode(payload.before_b64, validate=True)
            after_bytes = base64.b64decode(payload.after_b64, validate=True)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="proto inputs must be base64-encoded FileDescriptorSet blobs",
            ) from exc
        try:
            report = diff_contracts(
                kind="proto",
                before=before_bytes,
                after=after_bytes,
                ruleset=ruleset,
                session=db,
                spectral=False,
            )
        except Exception as exc:  # FileDescriptorSet parse error etc.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to parse FileDescriptorSet: {exc}",
            ) from exc

    # Persist the report so per-client migration guides can be retrieved
    # later via GET /guides/{diff_id}/{client_id}. The persisted row's
    # id is stamped onto the response so the caller never has to make a
    # second round-trip to learn it.
    row = ContractDiff(
        contract_kind=report.contract_kind,
        report_json=report.model_dump(mode="json"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    report = report.model_copy(update={"diff_id": row.id})

    log.info(
        "diff.computed",
        kind=payload.kind,
        diff_id=row.id,
        total=report.summary.total,
        breaking=report.summary.breaking,
        behavioral=report.summary.behavioral,
        additive=report.summary.additive,
    )
    return report
