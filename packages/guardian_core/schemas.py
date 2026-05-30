"""Pydantic v2 schemas for HTTP boundary."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ContractKind = Literal["openapi", "proto"]


class HealthResponse(BaseModel):
    """Payload returned by ``GET /healthz``."""

    model_config = ConfigDict(extra="forbid")

    version: str
    git_sha: str
    db_ok: bool
    redis_ok: bool


class ServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)]
    owner: Annotated[str, Field(min_length=1, max_length=255)]


class ServiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner: str
    created_at: datetime


class ContractUpload(BaseModel):
    """Payload for uploading a contract spec.

    For ``kind="openapi"``, ``spec`` MUST be a JSON object.
    For ``kind="proto"``, ``spec_b64`` MUST be a base64-encoded
    FileDescriptorSet.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)]
    kind: ContractKind
    spec: dict[str, Any] | None = None
    spec_b64: str | None = None
    spec_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("spec_b64")
    @classmethod
    def _validate_b64(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            base64.b64decode(v, validate=True)
        except Exception as exc:  # pragma: no cover - pydantic surface
            raise ValueError("spec_b64 must be valid base64") from exc
        return v


class ContractVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contract_id: str
    service_id: str
    version_hash: str
    spec_metadata: dict[str, Any]
    created_at: datetime


class ContractRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    service_id: str
    name: str
    kind: ContractKind
    version: ContractVersionRead
    created: bool = Field(
        description="True if a new version was created; False if existing version was returned."
    )


class TrafficIngestResponse(BaseModel):
    """Result returned by ``POST /ingest/traffic``."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str = Field(description="ID of the materialized de-facto contract row.")
    batch_id: str
    batch_hash: str
    service_id: str
    record_count: int
    observed_endpoint_count: int
    field_usage_row_count: int
    matched_endpoint_count: int
    is_duplicate_batch: bool


class DefactoContractRead(BaseModel):
    """A materialized de-facto contract returned by ``GET /defacto/{id}``."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    service_id: str
    ingest_batch_id: str
    endpoint_count: int
    observed_endpoint_count: int
    contract_json: dict[str, Any]
    materialized_at: datetime


CiConclusion = Literal[
    "success",
    "failure",
    "neutral",
    "action_required",
    "cancelled",
    "timed_out",
    "skipped",
]


class CiRunCreate(BaseModel):
    """Payload for ``POST /ci/runs``.

    Mirrors the row the Probot app upserts after each PR check. ``repo``
    is the ``owner/name`` slug, ``pr_number`` and ``head_sha`` form the
    natural key. ``report_json`` carries the full ChangeReport JSON.
    """

    model_config = ConfigDict(extra="forbid")

    repo: Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^/\s]+/[^/\s]+$")]
    pr_number: Annotated[int, Field(ge=1)]
    head_sha: Annotated[str, Field(min_length=7, max_length=64, pattern=r"^[0-9a-fA-F]+$")]
    base_sha: Annotated[str, Field(min_length=7, max_length=64, pattern=r"^[0-9a-fA-F]+$")]
    conclusion: CiConclusion
    report_json: dict[str, Any] = Field(default_factory=dict)
    bypass_label_present: bool = False
    check_run_id: int | None = None


class CiRunRead(BaseModel):
    """A persisted ``ci_runs`` row returned to API callers."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str
    conclusion: CiConclusion
    report_json: dict[str, Any]
    bypass_label_present: bool
    check_run_id: int | None
    created_at: datetime


class DiffRequest(BaseModel):
    """Payload for ``POST /diff``.

    Diff two contract versions and classify each change. ``kind`` selects
    the diff walker; ``before`` / ``after`` must match its expectations:

    * ``openapi`` — ``before_spec`` / ``after_spec`` must be JSON objects.
    * ``proto``   — ``before_b64`` / ``after_b64`` must be base64-encoded
      ``FileDescriptorSet`` blobs.

    A custom ruleset can be supplied as raw YAML in ``rules_yaml``; it
    merges over the default ruleset (rule-id-keyed last-write-wins).
    ``run_spectral`` is honored only for OpenAPI inputs and is a no-op
    when no Spectral binary is vendored.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ContractKind
    before_spec: dict[str, Any] | None = None
    after_spec: dict[str, Any] | None = None
    before_b64: str | None = None
    after_b64: str | None = None
    rules_yaml: str | None = None
    run_spectral: bool = False

    @field_validator("before_b64", "after_b64")
    @classmethod
    def _validate_b64(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            base64.b64decode(v, validate=True)
        except Exception as exc:  # pragma: no cover - pydantic surface
            raise ValueError("must be valid base64") from exc
        return v
