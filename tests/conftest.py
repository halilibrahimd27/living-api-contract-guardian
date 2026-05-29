"""Shared pytest fixtures.

Tests run against a per-test SQLite database. Alembic migrations are
exercised against that same database so we verify both the migration
DDL and the runtime app share one schema.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from guardian_core import db as guardian_db
from sqlalchemy import inspect

from alembic import command

ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'guardian-test.db'}"


@pytest.fixture()
def migrated_db(db_url: str) -> Iterator[str]:
    """Apply Alembic migrations to a fresh SQLite database."""
    os.environ["DATABASE_URL"] = db_url
    guardian_db.reset_engine()
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "head")
    yield db_url
    guardian_db.reset_engine()
    os.environ.pop("DATABASE_URL", None)


@pytest.fixture()
def client(migrated_db: str) -> Iterator[TestClient]:
    """FastAPI test client wired to the migrated database."""
    # Import inside fixture so configure_logging is invoked after env is set.
    from apps.api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def inspector(migrated_db: str) -> object:
    engine = guardian_db.get_engine()
    return inspect(engine)
