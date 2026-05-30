"""Top-level diff engine: raw-change walkers → classified ChangeReport."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from guardian_diff.clients import affected_clients
from guardian_diff.models import (
    ChangeRecord,
    ChangeReport,
    ChangeReportSummary,
    ContractKind,
    RawChange,
    SpectralFinding,
)
from guardian_diff.openapi import diff_openapi
from guardian_diff.proto import diff_proto
from guardian_diff.ruleset import RuleSet, load_default_rules
from guardian_diff.spectral import run_spectral


def _change_id(change: RawChange) -> str:
    payload = f"{change.kind}::{change.location}".encode()
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()[:16]


def _summarize(records: list[ChangeRecord]) -> ChangeReportSummary:
    summary = ChangeReportSummary(total=len(records))
    for r in records:
        if r.verdict == "additive":
            summary.additive += 1
        elif r.verdict == "behavioral":
            summary.behavioral += 1
        else:
            summary.breaking += 1
    return summary


def classify_changes(
    raw_changes: list[RawChange],
    *,
    ruleset: RuleSet | None = None,
    affected_clients_by_change: dict[int, list[str]] | None = None,
) -> list[ChangeRecord]:
    """Apply ``ruleset`` to ``raw_changes`` and return per-change ChangeRecords."""
    if ruleset is None:
        ruleset = load_default_rules()
    affected = affected_clients_by_change or {}
    records: list[ChangeRecord] = []
    for change in raw_changes:
        verdict, rule_id, rationale = ruleset.classify(change)
        records.append(
            ChangeRecord(
                change_id=_change_id(change),
                kind=change.kind,
                location=change.location,
                verdict=verdict,
                rule_id=rule_id,
                rationale=rationale,
                affected_clients=affected.get(id(change), []),
                before=change.before,
                after=change.after,
                detail=change.detail,
            )
        )
    return records


def diff_contracts(
    *,
    kind: ContractKind,
    before: Any,
    after: Any,
    ruleset: RuleSet | None = None,
    session: Session | None = None,
    spectral: bool = False,
) -> ChangeReport:
    """End-to-end: diff two contracts, classify, attach affected clients.

    ``before`` / ``after`` are typed by ``kind``:

    * ``openapi`` — both must be ``dict`` (the JSON-decoded spec)
    * ``proto`` — both must be ``bytes`` (a serialized FileDescriptorSet)

    ``ruleset`` defaults to the in-package YAML. ``session`` is only
    needed if you want ``affected_clients`` populated; without it the
    field is empty. ``spectral=True`` triggers the optional Spectral
    CLI integration for OpenAPI specs (no-op otherwise).
    """
    if ruleset is None:
        ruleset = load_default_rules()
    if kind == "openapi":
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise TypeError("openapi diff requires dict specs for both sides")
        raw_changes = diff_openapi(before, after)
        findings: list[SpectralFinding] = run_spectral(after) if spectral else []
    elif kind == "proto":
        if not isinstance(before, bytes) or not isinstance(after, bytes):
            raise TypeError("proto diff requires bytes FileDescriptorSets for both sides")
        raw_changes = diff_proto(before, after)
        findings = []
    else:  # pragma: no cover - guarded by Literal
        raise ValueError(f"unsupported contract kind: {kind!r}")

    affected = affected_clients(session, raw_changes) if session is not None else {}
    records = classify_changes(
        raw_changes,
        ruleset=ruleset,
        affected_clients_by_change=affected,
    )
    return ChangeReport(
        contract_kind=kind,
        changes=records,
        summary=_summarize(records),
        spectral_findings=findings,
        ruleset_id=ruleset.id,
    )
