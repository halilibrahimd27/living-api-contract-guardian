"""Traffic ingestion orchestrator.

Given a parsed HAR / gRPC payload, this module:

  1. Normalizes each request into an observed endpoint (URL → template).
  2. Builds incremental request/response schemas (genson) per endpoint.
  3. Upserts ``observed_endpoints`` rows (one per unique
     ``(service, method, path_template)``) with rolled-up counts and
     ``last_seen_at``.
  4. Upserts ``field_usages`` rows keyed by
     ``(endpoint, field_path, field_role, client_id, ingest_batch_hash)``
     so re-ingest of the same batch is idempotent.
  5. Materializes a ``defacto_contract`` snapshot tying the merged
     contract to the producing batch.

We use SQLAlchemy's dialect-specific ``insert(...).on_conflict_do_update``
when the target is Postgres, and SQLite's ``INSERT ... ON CONFLICT``
otherwise — both reach the same idempotency invariant.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from guardian_core.logging import get_logger
from guardian_core.models import (
    Contract,
    ContractVersion,
    DefactoContract,
    FieldUsage,
    IngestBatch,
    ObservedEndpoint,
)
from guardian_core.traffic._merge import merge_json_schemas
from guardian_core.traffic.defacto import build_defacto_contract
from guardian_core.traffic.grpc_parser import GrpcCallRecord, parse_grpc_log
from guardian_core.traffic.har_parser import HarRequestRecord, parse_har_bytes
from guardian_core.traffic.schema_inference import infer_schema, walk_field_paths
from guardian_core.traffic.url_match import RouteTree, build_route_tree, normalize_observed_path

log = get_logger(__name__)


@dataclass
class IngestResult:
    """Summary returned to the HTTP layer after an ingest."""

    batch_id: str
    batch_hash: str
    defacto_contract_id: str
    record_count: int
    observed_endpoint_count: int
    field_usage_row_count: int
    matched_endpoint_count: int
    is_duplicate_batch: bool


@dataclass
class _NormalizedRecord:
    method: str
    path_template: str
    matched: bool
    request_body: Any | None
    response_body: Any | None
    timestamp: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime) -> datetime:
    """Return ``dt`` with UTC tz if it was naive (SQLite round-trip case)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _max_timestamp(a: datetime, b: datetime) -> datetime:
    """Pick the later timestamp, normalizing tz-naive inputs to UTC.

    Returns the actual *object* (a or b) — caller can use identity to know
    which side won.
    """
    aa = _as_aware(a)
    bb = _as_aware(b)
    return a if aa >= bb else b


def _parse_timestamp(raw: str | None) -> datetime:
    if not raw:
        return _utcnow()
    try:
        # datetime.fromisoformat handles trailing 'Z' from Python 3.11+
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return _utcnow()


def compute_batch_hash(har_bytes: bytes | None, grpc_bytes: bytes | None) -> str:
    """Stable sha256 over the raw input payloads (HAR first, then gRPC)."""
    h = hashlib.sha256()
    if har_bytes:
        h.update(b"HAR\n")
        h.update(har_bytes)
    if grpc_bytes:
        h.update(b"\nGRPC\n")
        h.update(grpc_bytes)
    return h.hexdigest()


def _latest_openapi_spec(db: Session, service_id: str) -> dict[str, Any] | None:
    """Return the most recently-uploaded OpenAPI spec for a service, if any."""
    row = db.execute(
        select(ContractVersion, Contract)
        .join(Contract, Contract.id == ContractVersion.contract_id)
        .where(ContractVersion.service_id == service_id, Contract.kind == "openapi")
        .order_by(ContractVersion.created_at.desc())
    ).first()
    if row is None:
        return None
    version, _contract = row
    try:
        decoded = json.loads(version.canonical_blob.decode("utf-8"))
        if isinstance(decoded, dict):
            return decoded
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return None


def _normalize_har(
    records: Sequence[HarRequestRecord], tree: RouteTree | None
) -> list[_NormalizedRecord]:
    out: list[_NormalizedRecord] = []
    for r in records:
        template, matched = normalize_observed_path(r.url, r.method, tree)
        out.append(
            _NormalizedRecord(
                method=r.method,
                path_template=template,
                matched=matched is not None,
                request_body=r.request_body,
                response_body=r.response_body,
                timestamp=_parse_timestamp(r.timestamp),
            )
        )
    return out


def _normalize_grpc(records: Sequence[GrpcCallRecord]) -> list[_NormalizedRecord]:
    out: list[_NormalizedRecord] = []
    for r in records:
        # gRPC paths are already in /pkg.Service/Method form — no templating.
        out.append(
            _NormalizedRecord(
                method=r.method,
                path_template=r.path,
                matched=False,
                request_body=r.request_body,
                response_body=r.response_body,
                timestamp=_parse_timestamp(r.timestamp),
            )
        )
    return out


def _group_by_endpoint(
    records: Iterable[_NormalizedRecord],
) -> dict[tuple[str, str], list[_NormalizedRecord]]:
    grouped: dict[tuple[str, str], list[_NormalizedRecord]] = {}
    for r in records:
        grouped.setdefault((r.method, r.path_template), []).append(r)
    return grouped


def _upsert_observed_endpoint(
    db: Session,
    service_id: str,
    method: str,
    path_template: str,
    request_schema: dict[str, Any],
    response_schema: dict[str, Any],
    new_samples: int,
    last_seen_at: datetime,
    matched: bool,
) -> ObservedEndpoint:
    """Insert or update an ``observed_endpoints`` row in-place."""
    existing = db.scalar(
        select(ObservedEndpoint).where(
            ObservedEndpoint.service_id == service_id,
            ObservedEndpoint.method == method,
            ObservedEndpoint.path_template == path_template,
        )
    )
    if existing is None:
        existing = ObservedEndpoint(
            service_id=service_id,
            method=method,
            path_template=path_template,
            matched_endpoint_id=None,
            request_schema=request_schema,
            response_schema=response_schema,
            sample_count=new_samples,
            last_seen_at=last_seen_at,
        )
        db.add(existing)
        db.flush()
        return existing
    # Refine schemas by merging the new schema with what was stored.
    if request_schema:
        existing.request_schema = _merge_schema_dicts(existing.request_schema, request_schema)
    if response_schema:
        existing.response_schema = _merge_schema_dicts(existing.response_schema, response_schema)
    existing.sample_count = int(existing.sample_count or 0) + new_samples
    if _max_timestamp(last_seen_at, existing.last_seen_at) is last_seen_at:
        existing.last_seen_at = last_seen_at
    # Note: ``matched`` is captured per-batch in the defacto materializer;
    # we deliberately do not link ObservedEndpoint → Endpoint here because
    # endpoint rows live under a specific contract version and that
    # binding belongs to the contract-diff milestone.
    db.flush()
    return existing


# Backwards-compatible alias: in-tree call sites and the property-test
# suite both reference ``_merge_schema_dicts``; the canonical
# implementation lives in ``guardian_core.traffic._merge``.
_merge_schema_dicts = merge_json_schemas


def _bulk_upsert_field_usages(
    db: Session,
    rows: list[dict[str, Any]],
) -> None:
    """Idempotently insert ``field_usages`` rows.

    On conflict we refresh ``last_seen_at`` but DO NOT change ``count``:
    same batch = same observation = no double-counting.
    """
    if not rows:
        return
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    if dialect == "postgresql":
        stmt = pg_insert(FieldUsage).values(rows)
        update_cols = {"last_seen_at": stmt.excluded.last_seen_at}
        stmt = stmt.on_conflict_do_update(
            constraint="uq_field_usages_idempotency",
            set_=update_cols,
        )
        db.execute(stmt)
    else:
        stmt2 = sqlite_insert(FieldUsage).values(rows)
        update_cols2 = {"last_seen_at": stmt2.excluded.last_seen_at}
        stmt2 = stmt2.on_conflict_do_update(
            index_elements=[
                FieldUsage.endpoint_id,
                FieldUsage.field_path,
                FieldUsage.field_role,
                FieldUsage.client_id,
                FieldUsage.ingest_batch_hash,
            ],
            set_=update_cols2,
        )
        db.execute(stmt2)


def ingest_traffic(
    db: Session,
    *,
    service_id: str,
    har_bytes: bytes | None = None,
    grpc_bytes: bytes | None = None,
    client_id: str | None = None,
) -> IngestResult:
    """End-to-end traffic ingest.

    Reads HAR + gRPC payloads, infers schemas, upserts telemetry, and
    materializes a ``defacto_contract`` snapshot. Returns a result summary
    whose ``defacto_contract_id`` is what callers use to fetch the merged
    contract back.
    """
    if not har_bytes and not grpc_bytes:
        raise ValueError("ingest_traffic requires at least one of har_bytes / grpc_bytes")

    batch_hash = compute_batch_hash(har_bytes, grpc_bytes)
    existing_batch = db.scalar(
        select(IngestBatch).where(
            IngestBatch.service_id == service_id,
            IngestBatch.batch_hash == batch_hash,
        )
    )
    is_duplicate_batch = existing_batch is not None
    if existing_batch is not None:
        batch = existing_batch
    else:
        batch = IngestBatch(
            service_id=service_id,
            batch_hash=batch_hash,
            source="har+grpc" if (har_bytes and grpc_bytes) else ("har" if har_bytes else "grpc"),
            client_id=client_id,
            record_count=0,
        )
        db.add(batch)
        db.flush()

    har_records = parse_har_bytes(har_bytes) if har_bytes else []
    grpc_records = parse_grpc_log(grpc_bytes) if grpc_bytes else []

    spec = _latest_openapi_spec(db, service_id)
    tree = build_route_tree(spec) if spec else None
    normalized = _normalize_har(har_records, tree) + _normalize_grpc(grpc_records)

    grouped = _group_by_endpoint(normalized)
    field_usage_rows: list[dict[str, Any]] = []
    observed_summaries: list[dict[str, Any]] = []
    matched_count = 0

    for (method, template), records in grouped.items():
        request_samples = [r.request_body for r in records if r.request_body is not None]
        response_samples = [r.response_body for r in records if r.response_body is not None]
        req_schema = infer_schema(request_samples) if request_samples else {}
        resp_schema = infer_schema(response_samples) if response_samples else {}
        latest_ts = max((r.timestamp for r in records), default=_utcnow())
        matched_any = any(r.matched for r in records)
        if matched_any:
            matched_count += 1
        obs = _upsert_observed_endpoint(
            db,
            service_id=service_id,
            method=method,
            path_template=template,
            request_schema=req_schema,
            response_schema=resp_schema,
            new_samples=len(records),
            last_seen_at=latest_ts,
            matched=matched_any,
        )

        # Build field-usage rows.
        field_counts: dict[tuple[str, str], dict[str, Any]] = {}
        for rec in records:
            for body, role in (
                (rec.request_body, "request"),
                (rec.response_body, "response"),
            ):
                if body is None:
                    continue
                for path, jtype in walk_field_paths(body):
                    key = (path, role)
                    entry = field_counts.setdefault(
                        key,
                        {
                            "field_path": path,
                            "field_role": role,
                            "count": 0,
                            "json_types": {},
                            "last_seen_at": rec.timestamp,
                        },
                    )
                    entry["count"] = int(entry["count"]) + 1
                    types_map = entry["json_types"]
                    if isinstance(types_map, dict):
                        types_map[jtype] = int(types_map.get(jtype, 0)) + 1
                    entry["last_seen_at"] = _max_timestamp(rec.timestamp, entry["last_seen_at"])
        for entry in field_counts.values():
            field_usage_rows.append(
                {
                    "endpoint_id": obs.id,
                    "field_path": entry["field_path"],
                    "field_role": entry["field_role"],
                    "client_id": client_id,
                    "ingest_batch_hash": batch_hash,
                    "count": entry["count"],
                    "last_seen_at": entry["last_seen_at"],
                    "json_types": entry["json_types"],
                }
            )

        observed_summaries.append(
            {
                "method": method,
                "path_template": template,
                "request_schema": obs.request_schema,
                "response_schema": obs.response_schema,
                "sample_count": obs.sample_count,
                "last_seen_at": obs.last_seen_at.isoformat() if obs.last_seen_at else None,
                "matched": matched_any,
            }
        )

    _bulk_upsert_field_usages(db, field_usage_rows)

    batch.record_count = (
        (batch.record_count or 0) + len(normalized) if is_duplicate_batch else len(normalized)
    )
    db.flush()

    contract_json = build_defacto_contract(spec, observed_summaries)
    static_paths = list((spec or {}).get("paths", {}).keys()) if spec else []
    defacto = DefactoContract(
        service_id=service_id,
        ingest_batch_id=batch.id,
        contract_json=contract_json,
        endpoint_count=len(static_paths),
        observed_endpoint_count=len(observed_summaries),
    )
    db.add(defacto)
    db.flush()

    log.info(
        "traffic.ingested",
        service_id=service_id,
        batch_id=batch.id,
        batch_hash=batch_hash,
        record_count=len(normalized),
        observed_endpoints=len(observed_summaries),
        field_usage_rows=len(field_usage_rows),
        matched_endpoints=matched_count,
        duplicate=is_duplicate_batch,
        defacto_contract_id=defacto.id,
    )

    return IngestResult(
        batch_id=batch.id,
        batch_hash=batch_hash,
        defacto_contract_id=defacto.id,
        record_count=len(normalized),
        observed_endpoint_count=len(observed_summaries),
        field_usage_row_count=len(field_usage_rows),
        matched_endpoint_count=matched_count,
        is_duplicate_batch=is_duplicate_batch,
    )
