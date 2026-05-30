"""Pydantic v2 models for the evolution rule engine."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# A verdict is the human-meaningful classification of a single contract
# change. Order matters only for reporting purposes — see
# :func:`guardian_diff.engine.summarize`.
Verdict = Literal["additive", "behavioral", "breaking"]

ContractKind = Literal["openapi", "proto"]


class RawChange(BaseModel):
    """A single, unclassified contract delta.

    Emitted by the OpenAPI / protobuf diff walkers. Each row carries a
    canonical, machine-stable ``kind`` (e.g. ``operation.removed`` or
    ``proto.field.number_changed``) used by the rule engine to look up a
    verdict; ``location`` is a human-readable JSON-Pointer-ish path that
    also serves as the join key into the inferred-endpoint catalogue.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Annotated[str, Field(min_length=1, max_length=128)]
    location: Annotated[str, Field(min_length=1, max_length=2048)]
    before: object | None = None
    after: object | None = None
    detail: dict[str, object] = Field(default_factory=dict)


class ChangeRecord(BaseModel):
    """A raw change after the rule engine has classified it.

    ``change_id`` is a stable, deterministic identifier (sha1 over
    ``kind`` + ``location``) so the same diff produces the same ids
    across runs — the API can return them safely as primary keys.
    """

    model_config = ConfigDict(extra="forbid")

    change_id: Annotated[str, Field(min_length=8, max_length=64)]
    kind: str
    location: str
    verdict: Verdict
    rule_id: str
    rationale: str
    affected_clients: list[str] = Field(default_factory=list)
    before: object | None = None
    after: object | None = None
    detail: dict[str, object] = Field(default_factory=dict)


class ChangeReportSummary(BaseModel):
    """Aggregate counters across a ChangeReport."""

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    additive: int = 0
    behavioral: int = 0
    breaking: int = 0


class SpectralFinding(BaseModel):
    """A single finding from the Spectral lint CLI, if one was invoked."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: int
    path: list[str] = Field(default_factory=list)


class ChangeReport(BaseModel):
    """Top-level diff result.

    ``contract_kind`` is the input contract family (``openapi`` /
    ``proto``); ``changes`` is the per-change verdict list; ``summary``
    rolls them up so a deploy gate can short-circuit on
    ``summary.breaking > 0``. ``spectral_findings`` is non-empty only
    when an OpenAPI input was linted by a vendored Spectral CLI.
    """

    model_config = ConfigDict(extra="forbid")

    contract_kind: ContractKind
    changes: list[ChangeRecord] = Field(default_factory=list)
    summary: ChangeReportSummary = Field(default_factory=ChangeReportSummary)
    spectral_findings: list[SpectralFinding] = Field(default_factory=list)
    ruleset_id: str = "default"
    diff_id: str | None = Field(
        default=None,
        description=(
            "Persisted ``contract_diffs.id`` for this report. Populated by"
            " POST /diff so callers can hand the id off to"
            " GET /guides/{diff_id}/{client_id}. Unset for reports built"
            " purely in-process (e.g. CLI invocations of"
            " ``guardian_diff.diff_contracts``)."
        ),
    )
