"""SQLAlchemy 2.0 ORM models for Guardian core entities.

Identifiers are UUID7 strings (time-ordered) generated via ``uuid_utils``.
JSON columns transparently map to ``JSONB`` on PostgreSQL and ``JSON``
elsewhere (e.g. SQLite for tests).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import uuid_utils
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    """Generate a UUID7 string id (time-ordered for clustered inserts)."""
    return str(uuid_utils.uuid7())


JsonDict = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Declarative base for all Guardian ORM models."""


class Service(Base):
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    contracts: Mapped[list[Contract]] = relationship(
        "Contract", back_populates="service", cascade="all, delete-orphan"
    )
    contract_versions: Mapped[list[ContractVersion]] = relationship(
        "ContractVersion", back_populates="service", cascade="all, delete-orphan"
    )
    endpoints: Mapped[list[Endpoint]] = relationship(
        "Endpoint", back_populates="service", cascade="all, delete-orphan"
    )


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (UniqueConstraint("service_id", "name", name="uq_contracts_service_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    service_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    service: Mapped[Service] = relationship("Service", back_populates="contracts")
    versions: Mapped[list[ContractVersion]] = relationship(
        "ContractVersion", back_populates="contract", cascade="all, delete-orphan"
    )


class ContractVersion(Base):
    __tablename__ = "contract_versions"
    __table_args__ = (
        UniqueConstraint("service_id", "version_hash", name="uq_versions_service_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    contract_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    canonical_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    spec_metadata: Mapped[dict[str, Any]] = mapped_column(JsonDict, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    contract: Mapped[Contract] = relationship("Contract", back_populates="versions")
    service: Mapped[Service] = relationship("Service", back_populates="contract_versions")
    endpoints: Mapped[list[Endpoint]] = relationship(
        "Endpoint", back_populates="contract_version", cascade="all, delete-orphan"
    )
    deprecations: Mapped[list[Deprecation]] = relationship(
        "Deprecation", back_populates="contract_version", cascade="all, delete-orphan"
    )


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    usages: Mapped[list[Usage]] = relationship(
        "Usage", back_populates="client", cascade="all, delete-orphan"
    )


class Endpoint(Base):
    """A single operation extracted from a contract version.

    For OpenAPI: ``(method, path)`` keys an operation.
    For Protobuf: ``method`` carries the RPC verb and ``path`` is the
    fully-qualified rpc name (``package.Service/Method``).
    """

    __tablename__ = "endpoints"
    __table_args__ = (
        UniqueConstraint(
            "contract_version_id",
            "method",
            "path",
            name="uq_endpoints_version_method_path",
        ),
        Index("ix_endpoints_service", "service_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    contract_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_excerpt: Mapped[dict[str, Any]] = mapped_column(JsonDict, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    service: Mapped[Service] = relationship("Service", back_populates="endpoints")
    contract_version: Mapped[ContractVersion] = relationship(
        "ContractVersion", back_populates="endpoints"
    )
    usages: Mapped[list[Usage]] = relationship(
        "Usage", back_populates="endpoint", cascade="all, delete-orphan"
    )


class Usage(Base):
    """Per-client observation of an endpoint over a time window."""

    __tablename__ = "usages"
    __table_args__ = (
        UniqueConstraint(
            "endpoint_id",
            "client_id",
            "window_start",
            name="uq_usages_endpoint_client_window",
        ),
        Index("ix_usages_endpoint", "endpoint_id"),
        Index("ix_usages_client", "client_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    endpoint_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    endpoint: Mapped[Endpoint] = relationship("Endpoint", back_populates="usages")
    client: Mapped[Client] = relationship("Client", back_populates="usages")


class InferredEndpoint(Base):
    """A client-side API call site discovered by the static AST miner.

    Rows are content-hashed so re-runs of the miner against the same repo
    + commit SHA are idempotent: a row is identified by
    ``(repo, commit_sha, content_hash)`` and re-mining upserts the same
    record. ``fields`` carries the inferred query/body parameter names.
    """

    __tablename__ = "inferred_endpoints"
    __table_args__ = (
        UniqueConstraint(
            "repo",
            "commit_sha",
            "content_hash",
            name="uq_inferred_endpoints_repo_commit_hash",
        ),
        Index("ix_inferred_endpoints_repo_commit", "repo", "commit_sha"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    repo: Mapped[str] = mapped_column(String(512), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    file: Mapped[str] = mapped_column(String(1024), nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    client_library: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    path_template: Mapped[str] = mapped_column(String(1024), nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JsonDict, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )


class IngestBatch(Base):
    """One traffic-ingest invocation: HAR upload, gRPC log dump, etc.

    The ``batch_hash`` is a stable content hash over the raw payload; the
    ``(service_id, batch_hash)`` unique constraint lets re-uploading the
    exact same traffic dump be detected and de-duplicated by the ingestor.
    """

    __tablename__ = "ingest_batches"
    __table_args__ = (
        UniqueConstraint("service_id", "batch_hash", name="uq_ingest_batches_service_hash"),
        Index("ix_ingest_batches_service", "service_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    service_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    batch_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    client_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )


class ObservedEndpoint(Base):
    """An endpoint inferred from traffic and merged across ingest batches.

    Keyed by ``(service_id, method, path_template)``. ``request_schema`` and
    ``response_schema`` are JSON Schemas built incrementally with genson;
    ``sample_count`` and ``last_seen_at`` are roll-up telemetry.
    ``matched_endpoint_id`` is the static-contract endpoint this observed
    endpoint resolved against (NULL if no static match — i.e. drift).
    """

    __tablename__ = "observed_endpoints"
    __table_args__ = (
        UniqueConstraint(
            "service_id",
            "method",
            "path_template",
            name="uq_observed_endpoints_service_method_path",
        ),
        Index("ix_observed_endpoints_service", "service_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    service_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    path_template: Mapped[str] = mapped_column(String(1024), nullable=False)
    matched_endpoint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_schema: Mapped[dict[str, Any]] = mapped_column(JsonDict, nullable=False, default=dict)
    response_schema: Mapped[dict[str, Any]] = mapped_column(JsonDict, nullable=False, default=dict)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )


class FieldUsage(Base):
    """Per-field telemetry row, idempotent over a single ingest batch.

    Each row records the count + last-seen timestamp for a JSON field path
    (e.g. ``$.body.user.email``) under a specific observed endpoint, for a
    specific client, in a specific ingest batch. The unique constraint on
    ``(endpoint_id, field_path, field_role, client_id, ingest_batch_hash)``
    means re-ingesting the exact same batch is a no-op for counts and only
    refreshes ``last_seen_at``.
    """

    __tablename__ = "field_usages"
    __table_args__ = (
        UniqueConstraint(
            "endpoint_id",
            "field_path",
            "field_role",
            "client_id",
            "ingest_batch_hash",
            name="uq_field_usages_idempotency",
        ),
        Index("ix_field_usages_endpoint", "endpoint_id"),
        Index("ix_field_usages_endpoint_path", "endpoint_id", "field_path"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    endpoint_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("observed_endpoints.id", ondelete="CASCADE"), nullable=False
    )
    field_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    field_role: Mapped[str] = mapped_column(String(16), nullable=False)
    client_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ingest_batch_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    json_types: Mapped[dict[str, Any]] = mapped_column(JsonDict, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )


class DefactoContract(Base):
    """A materialized 'de-facto' contract: static spec merged with traffic.

    One row per ``POST /ingest/traffic`` invocation; ``contract_json``
    contains the merged OpenAPI-shaped document at the moment the batch
    was ingested. ``ingest_batch_id`` is the producing batch.
    """

    __tablename__ = "defacto_contracts"
    __table_args__ = (Index("ix_defacto_contracts_service", "service_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    service_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    ingest_batch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ingest_batches.id", ondelete="CASCADE"), nullable=False
    )
    contract_json: Mapped[dict[str, Any]] = mapped_column(JsonDict, nullable=False, default=dict)
    endpoint_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_endpoint_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    materialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )


class Deprecation(Base):
    """A deprecation notice for an endpoint, tied to a contract version."""

    __tablename__ = "deprecations"
    __table_args__ = (
        UniqueConstraint(
            "contract_version_id",
            "endpoint_id",
            name="uq_deprecations_version_endpoint",
        ),
        Index("ix_deprecations_endpoint", "endpoint_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    contract_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False
    )
    endpoint_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sunset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[dict[str, Any]] = mapped_column(JsonDict, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    contract_version: Mapped[ContractVersion] = relationship(
        "ContractVersion", back_populates="deprecations"
    )
    endpoint: Mapped[Endpoint] = relationship("Endpoint")
