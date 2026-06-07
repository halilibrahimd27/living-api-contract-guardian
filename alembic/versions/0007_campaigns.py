"""add campaigns, campaign_metrics, reminder_prs tables for deprecation campaign orchestrator

Revision ID: 0007_campaigns
Revises: 0006_guides
Create Date: 2026-06-07

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0007_campaigns"
down_revision: str | None = "0006_guides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "endpoint_id",
            sa.String(length=36),
            sa.ForeignKey("endpoints.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("field_path", sa.String(length=1024), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("usage_threshold_pct", sa.Float(), nullable=False, server_default="5.0"),
        sa.Column("decay_window_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("peak_usage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("github_repo", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_campaigns_state", "campaigns", ["state"])

    op.create_table(
        "campaign_metrics",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ewma_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("remaining_client_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_campaign_metrics_campaign_sampled",
        "campaign_metrics",
        ["campaign_id", "sampled_at"],
    )

    op.create_table(
        "reminder_prs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_repo", sa.String(length=512), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("branch_name", sa.String(length=512), nullable=False),
        sa.Column("pr_state", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("campaign_id", "client_repo", name="uq_reminder_prs_campaign_repo"),
    )
    op.create_index("ix_reminder_prs_campaign", "reminder_prs", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_reminder_prs_campaign", table_name="reminder_prs")
    op.drop_table("reminder_prs")
    op.drop_index(
        "ix_campaign_metrics_campaign_sampled", table_name="campaign_metrics"
    )
    op.drop_table("campaign_metrics")
    op.drop_index("ix_campaigns_state", table_name="campaigns")
    op.drop_table("campaigns")
