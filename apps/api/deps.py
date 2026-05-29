"""FastAPI dependency providers."""

from __future__ import annotations

from collections.abc import Iterator

from guardian_core.db import get_sessionmaker
from sqlalchemy.orm import Session


def get_db() -> Iterator[Session]:
    """Yield a SQLAlchemy session bound to the global engine."""
    factory = get_sessionmaker()
    session = factory()
    try:
        yield session
    finally:
        session.close()
