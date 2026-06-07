"""add inferred_endpoints table

Revision ID: 0003_inferred_endpoints
Revises: 0002_endpoints_usages
Create Date: 2026-05-29

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0003_inferred_endpoints"
down_revision: str | None = "0002_endpoints_usages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine[object]:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "inferred_endpoints",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("repo", sa.String(length=512), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("file", sa.String(length=1024), nullable=False),
        sa.Column("line", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("client_library", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("path_template", sa.String(length=1024), nullable=False),
        sa.Column("fields", _json_type(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "repo",
            "commit_sha",
            "content_hash",
            name="uq_inferred_endpoints_repo_commit_hash",
        ),
    )
    op.create_index(
        "ix_inferred_endpoints_repo_commit",
        "inferred_endpoints",
        ["repo", "commit_sha"],
    )


def downgrade() -> None:
    op.drop_index("ix_inferred_endpoints_repo_commit", table_name="inferred_endpoints")
    op.drop_table("inferred_endpoints")
