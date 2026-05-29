"""SQLAlchemy 2.0 ORM models for Guardian core entities."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
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
    return str(uuid.uuid4())


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


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
