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
