"""Traffic-replay ingestion route: ``POST /ingest/traffic``.

Accepts an HTTP Archive (HAR) file and/or a JSON-lines gRPC call log,
infers schemas + URL templates, persists field-level telemetry, and
materializes a de-facto contract that fuses the static OpenAPI spec
with the observed traffic. Returns the merged contract id.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from guardian_core.logging import get_logger
from guardian_core.models import DefactoContract, Service
from guardian_core.schemas import DefactoContractRead, TrafficIngestResponse
from guardian_core.traffic.ingestor import ingest_traffic
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import get_db

router = APIRouter(prefix="/ingest", tags=["ingest"])
log = get_logger(__name__)

# Cap to avoid pathological uploads: 64 MiB per stream.
_MAX_BODY_BYTES = 64 * 1024 * 1024


async def _read_capped(upload: UploadFile | None) -> bytes | None:
    """Read an ``UploadFile`` body, rejecting payloads above ``_MAX_BODY_BYTES``.

    ``UploadFile`` exposes an async ``read()`` which the FastAPI worker
    backs with ``SpooledTemporaryFile``, so this is streaming-friendly:
    we don't buffer two copies of the upload in memory.
    """
    if upload is None:
        return None
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"upload exceeds {_MAX_BODY_BYTES} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks) if chunks else None


@router.post(
    "/traffic",
    response_model=TrafficIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_traffic_route(
    service_id: str = Form(..., description="Target service id."),
    client_id: str | None = Form(None, description="Optional client id for telemetry attribution."),
    har: UploadFile | None = File(None, description="HAR (HTTP Archive) upload."),
    grpc_log: UploadFile | None = File(None, description="JSONL gRPC call log upload."),
    db: Session = Depends(get_db),
) -> TrafficIngestResponse:
    """Ingest HAR / gRPC traffic and return the merged contract id."""
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="service not found")

    har_bytes = await _read_capped(har)
    grpc_bytes = await _read_capped(grpc_log)
    if not har_bytes and not grpc_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="at least one of 'har' or 'grpc_log' is required",
        )

    try:
        result = ingest_traffic(
            db,
            service_id=service_id,
            har_bytes=har_bytes,
            grpc_bytes=grpc_bytes,
            client_id=client_id,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        log.warning("ingest.traffic.invalid", service_id=service_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid traffic payload",
        ) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ingest batch conflict",
        ) from exc

    log.info(
        "ingest.traffic.ok",
        service_id=service_id,
        contract_id=result.defacto_contract_id,
        record_count=result.record_count,
        duplicate=result.is_duplicate_batch,
    )
    return TrafficIngestResponse(
        contract_id=result.defacto_contract_id,
        batch_id=result.batch_id,
        batch_hash=result.batch_hash,
        service_id=service_id,
        record_count=result.record_count,
        observed_endpoint_count=result.observed_endpoint_count,
        field_usage_row_count=result.field_usage_row_count,
        matched_endpoint_count=result.matched_endpoint_count,
        is_duplicate_batch=result.is_duplicate_batch,
    )


@router.get(
    "/defacto/{contract_id}",
    response_model=DefactoContractRead,
)
def get_defacto_contract(contract_id: str, db: Session = Depends(get_db)) -> DefactoContractRead:
    """Fetch a previously-materialized de-facto contract."""
    row = db.get(DefactoContract, contract_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="defacto contract not found"
        )
    return DefactoContractRead.model_validate(row)
