"""add endpoints, usages, deprecations tables

Revision ID: 0002_endpoints_usages
Revises: 0001_initial
Create Date: 2026-05-29

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0002_endpoints_usages"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "endpoints",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "contract_version_id",
            sa.String(length=36),
            sa.ForeignKey("contract_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            sa.String(length=36),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("operation_id", sa.String(length=255), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("spec_excerpt", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "contract_version_id",
            "method",
            "path",
            name="uq_endpoints_version_method_path",
        ),
    )
    op.create_index("ix_endpoints_service", "endpoints", ["service_id"])

    op.create_table(
        "usages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "endpoint_id",
            sa.String(length=36),
            sa.ForeignKey("endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.String(length=36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="manual"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "endpoint_id",
            "client_id",
            "window_start",
            name="uq_usages_endpoint_client_window",
        ),
    )
    op.create_index("ix_usages_endpoint", "usages", ["endpoint_id"])
    op.create_index("ix_usages_client", "usages", ["client_id"])

    op.create_table(
        "deprecations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "contract_version_id",
            sa.String(length=36),
            sa.ForeignKey("contract_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "endpoint_id",
            sa.String(length=36),
            sa.ForeignKey("endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="proposed"),
        sa.Column("reason", sa.String(length=1024), nullable=True),
        sa.Column("sunset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "contract_version_id",
            "endpoint_id",
            name="uq_deprecations_version_endpoint",
        ),
    )
    op.create_index("ix_deprecations_endpoint", "deprecations", ["endpoint_id"])


def downgrade() -> None:
    op.drop_index("ix_deprecations_endpoint", table_name="deprecations")
    op.drop_table("deprecations")
    op.drop_index("ix_usages_client", table_name="usages")
    op.drop_index("ix_usages_endpoint", table_name="usages")
    op.drop_table("usages")
    op.drop_index("ix_endpoints_service", table_name="endpoints")
    op.drop_table("endpoints")
