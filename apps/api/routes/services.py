"""Service registration and contract upload routes."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, status
from guardian_core.hashing import (
    canonicalize_openapi,
    canonicalize_proto,
    compute_version_hash,
)
from guardian_core.logging import get_logger
from guardian_core.models import Contract, ContractVersion, Service
from guardian_core.schemas import (
    ContractRead,
    ContractUpload,
    ContractVersionRead,
    ServiceCreate,
    ServiceRead,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import get_db

router = APIRouter(prefix="/services", tags=["services"])
log = get_logger(__name__)


@router.post("", response_model=ServiceRead, status_code=status.HTTP_201_CREATED)
def create_service(payload: ServiceCreate, db: Session = Depends(get_db)) -> ServiceRead:
    """Register a new Service."""
    service = Service(name=payload.name, owner=payload.owner)
    db.add(service)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"service with name '{payload.name}' already exists",
        ) from exc
    db.refresh(service)
    log.info("service.created", service_id=service.id, name=service.name)
    return ServiceRead.model_validate(service)


@router.get("/{service_id}", response_model=ServiceRead)
def get_service(service_id: str, db: Session = Depends(get_db)) -> ServiceRead:
    """Fetch a Service by id."""
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="service not found")
    return ServiceRead.model_validate(service)


def _materialize_spec_bytes(payload: ContractUpload) -> tuple[bytes, bytes]:
    """Return (raw_bytes, canonical_bytes) for a contract upload."""
    if payload.kind == "openapi":
        if payload.spec is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="openapi contracts require 'spec' (a JSON object)",
            )
        canonical = canonicalize_openapi(payload.spec)
        return canonical, canonical
    # proto
    if payload.spec_b64 is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="proto contracts require 'spec_b64' (base64 FileDescriptorSet)",
        )
    raw = base64.b64decode(payload.spec_b64, validate=True)
    return raw, canonicalize_proto(raw)


@router.post(
    "/{service_id}/contracts",
    response_model=ContractRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_contract(
    service_id: str,
    payload: ContractUpload,
    db: Session = Depends(get_db),
) -> ContractRead:
    """Upload (or idempotently re-upload) a contract spec for a service."""
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="service not found")

    raw_bytes, canonical_bytes = _materialize_spec_bytes(payload)
    version_hash = compute_version_hash(canonical_bytes)

    # Idempotency: existing (service_id, version_hash) -> return the prior version.
    existing_version = db.scalar(
        select(ContractVersion).where(
            ContractVersion.service_id == service_id,
            ContractVersion.version_hash == version_hash,
        )
    )
    if existing_version is not None:
        contract = db.get(Contract, existing_version.contract_id)
        assert contract is not None
        log.info(
            "contract.version.exists",
            service_id=service_id,
            contract_id=contract.id,
            version_hash=version_hash,
        )
        return ContractRead(
            id=contract.id,
            service_id=service_id,
            name=contract.name,
            kind=contract.kind,
            version=ContractVersionRead.model_validate(existing_version),
            created=False,
        )

    contract = db.scalar(
        select(Contract).where(Contract.service_id == service_id, Contract.name == payload.name)
    )
    if contract is None:
        contract = Contract(service_id=service_id, name=payload.name, kind=payload.kind)
        db.add(contract)
        db.flush()
    elif contract.kind != payload.kind:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"contract '{payload.name}' already exists with kind '{contract.kind}', "
                f"cannot accept '{payload.kind}'"
            ),
        )

    version = ContractVersion(
        contract_id=contract.id,
        service_id=service_id,
        version_hash=version_hash,
        raw_blob=raw_bytes,
        canonical_blob=canonical_bytes,
        spec_metadata=payload.spec_metadata,
    )
    db.add(version)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="contract version conflict",
        ) from exc
    db.refresh(contract)
    db.refresh(version)
    log.info(
        "contract.version.created",
        service_id=service_id,
        contract_id=contract.id,
        version_hash=version_hash,
    )
    return ContractRead(
        id=contract.id,
        service_id=service_id,
        name=contract.name,
        kind=contract.kind,
        version=ContractVersionRead.model_validate(version),
        created=True,
    )
