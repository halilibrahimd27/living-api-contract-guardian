"""add traffic-replay tables: ingest_batches, observed_endpoints, field_usages, defacto_contracts

Revision ID: 0004_traffic_replay
Revises: 0003_inferred_endpoints
Create Date: 2026-05-29

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0004_traffic_replay"
down_revision: str | None = "0003_inferred_endpoints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "ingest_batches",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "service_id",
            sa.String(length=36),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("batch_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("service_id", "batch_hash", name="uq_ingest_batches_service_hash"),
    )
    op.create_index("ix_ingest_batches_service", "ingest_batches", ["service_id"])

    op.create_table(
        "observed_endpoints",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "service_id",
            sa.String(length=36),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("path_template", sa.String(length=1024), nullable=False),
        sa.Column("matched_endpoint_id", sa.String(length=36), nullable=True),
        sa.Column("request_schema", _json_type(), nullable=False),
        sa.Column("response_schema", _json_type(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "service_id",
            "method",
            "path_template",
            name="uq_observed_endpoints_service_method_path",
        ),
    )
    op.create_index("ix_observed_endpoints_service", "observed_endpoints", ["service_id"])

    op.create_table(
        "field_usages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "endpoint_id",
            sa.String(length=36),
            sa.ForeignKey("observed_endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_path", sa.String(length=1024), nullable=False),
        sa.Column("field_role", sa.String(length=16), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=True),
        sa.Column("ingest_batch_hash", sa.String(length=64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("json_types", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "endpoint_id",
            "field_path",
            "field_role",
            "client_id",
            "ingest_batch_hash",
            name="uq_field_usages_idempotency",
        ),
    )
    op.create_index("ix_field_usages_endpoint", "field_usages", ["endpoint_id"])
    op.create_index(
        "ix_field_usages_endpoint_path",
        "field_usages",
        ["endpoint_id", "field_path"],
    )

    op.create_table(
        "defacto_contracts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "service_id",
            sa.String(length=36),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ingest_batch_id",
            sa.String(length=36),
            sa.ForeignKey("ingest_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contract_json", _json_type(), nullable=False),
        sa.Column("endpoint_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_endpoint_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "materialized_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_defacto_contracts_service", "defacto_contracts", ["service_id"])


def downgrade() -> None:
    op.drop_index("ix_defacto_contracts_service", table_name="defacto_contracts")
    op.drop_table("defacto_contracts")
    op.drop_index("ix_field_usages_endpoint_path", table_name="field_usages")
    op.drop_index("ix_field_usages_endpoint", table_name="field_usages")
    op.drop_table("field_usages")
    op.drop_index("ix_observed_endpoints_service", table_name="observed_endpoints")
    op.drop_table("observed_endpoints")
    op.drop_index("ix_ingest_batches_service", table_name="ingest_batches")
    op.drop_table("ingest_batches")
