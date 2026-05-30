"""add ci_runs table for the GitHub App / Action integration

Revision ID: 0005_ci_runs
Revises: 0004_traffic_replay
Create Date: 2026-05-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0005_ci_runs"
down_revision: str | None = "0004_traffic_replay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "ci_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(length=64), nullable=False),
        sa.Column("base_sha", sa.String(length=64), nullable=False),
        sa.Column("conclusion", sa.String(length=32), nullable=False),
        sa.Column("report_json", _json_type(), nullable=False),
        sa.Column(
            "bypass_label_present",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("check_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "repo",
            "pr_number",
            "head_sha",
            name="uq_ci_runs_repo_pr_sha",
        ),
    )
    op.create_index("ix_ci_runs_repo_pr", "ci_runs", ["repo", "pr_number"])


def downgrade() -> None:
    op.drop_index("ix_ci_runs_repo_pr", table_name="ci_runs")
    op.drop_table("ci_runs")
