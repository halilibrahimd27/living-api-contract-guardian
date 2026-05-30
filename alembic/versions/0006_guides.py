"""add contract_diffs + guides tables for LLM-drafted per-client migration guides

Revision ID: 0006_guides
Revises: 0005_ci_runs
Create Date: 2026-05-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0006_guides"
down_revision: str | None = "0005_ci_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "contract_diffs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("contract_kind", sa.String(length=32), nullable=False),
        sa.Column("report_json", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_contract_diffs_created", "contract_diffs", ["created_at"])

    op.create_table(
        "guides",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "diff_id",
            sa.String(length=36),
            sa.ForeignKey("contract_diffs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_id", sa.String(length=512), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("prompt_hash", name="uq_guides_prompt_hash"),
    )
    op.create_index("ix_guides_diff_client", "guides", ["diff_id", "client_id"])


def downgrade() -> None:
    op.drop_index("ix_guides_diff_client", table_name="guides")
    op.drop_table("guides")
    op.drop_index("ix_contract_diffs_created", table_name="contract_diffs")
    op.drop_table("contract_diffs")
