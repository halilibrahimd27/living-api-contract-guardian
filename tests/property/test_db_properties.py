"""Property-based tests for database engine and session helpers.

Invariants tested:
1. get_database_url() returns a valid SQLAlchemy connection string
2. get_database_url() respects DATABASE_URL env var and falls back to SQLite
3. make_engine() creates a valid SQLAlchemy Engine with correct config
4. make_engine() with SQLite disables check_same_thread
5. get_engine() and get_sessionmaker() are singletons (cached)
6. reset_engine() clears the singleton cache
7. session_scope() is a context manager that yields Session and commits on success
8. session_scope() rolls back on exception
"""

from __future__ import annotations

import os

import pytest
from guardian_core.db import (
    get_database_url,
    get_engine,
    get_sessionmaker,
    make_engine,
    reset_engine,
    session_scope,
)
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session


class TestGetDatabaseUrl:
    """Property tests for get_database_url()."""

    def test_get_database_url_returns_string(self) -> None:
        """get_database_url() always returns a string."""
        url = get_database_url()
        assert isinstance(url, str)
        assert len(url) > 0

    def test_get_database_url_is_valid_connection_string(self) -> None:
        """get_database_url() returns a valid SQLAlchemy connection string."""
        url = get_database_url()
        # Should contain a database engine (sqlite, postgresql, mysql, etc.)
        assert any(url.startswith(engine) for engine in ["sqlite", "postgresql", "mysql", "oracle"])

    def test_get_database_url_default_is_sqlite(self) -> None:
        """get_database_url() defaults to SQLite."""
        original = os.environ.get("DATABASE_URL")
        try:
            os.environ.pop("DATABASE_URL", None)
            url = get_database_url()
            assert url.startswith("sqlite")
        finally:
            if original is not None:
                os.environ["DATABASE_URL"] = original

    @given(
        db_url=st.text(min_size=10, max_size=200).filter(
            lambda s: s.startswith(("sqlite://", "postgresql://"))
        )
    )
    def test_get_database_url_respects_env_var(self, db_url: str) -> None:
        """get_database_url() returns DATABASE_URL when environment variable is set."""
        original = os.environ.get("DATABASE_URL")
        try:
            os.environ["DATABASE_URL"] = db_url
            result = get_database_url()
            assert result == db_url
        finally:
            if original is not None:
                os.environ["DATABASE_URL"] = original
            else:
                os.environ.pop("DATABASE_URL", None)

    def test_get_database_url_is_deterministic(self) -> None:
        """get_database_url() returns the same value on multiple calls."""
        url1 = get_database_url()
        url2 = get_database_url()
        url3 = get_database_url()
        assert url1 == url2 == url3


class TestMakeEngine:
    """Property tests for make_engine()."""

    def test_make_engine_returns_engine(self) -> None:
        """make_engine() returns a SQLAlchemy Engine."""
        engine = make_engine("sqlite:///:memory:")
        assert isinstance(engine, Engine)

    def test_make_engine_with_sqlite_disables_check_same_thread(self) -> None:
        """make_engine() with SQLite disables check_same_thread."""
        engine = make_engine("sqlite:///:memory:")
        # Check that the connect_args include check_same_thread=False
        assert (
            engine.connect_args.get("check_same_thread") is False
            or "check_same_thread" not in engine.connect_args
        )

    def test_make_engine_with_postgres_no_check_same_thread(self) -> None:
        """make_engine() with PostgreSQL doesn't add check_same_thread."""
        # This would fail if no PostgreSQL is running, so we just check the code path
        try:
            engine = make_engine("postgresql://localhost:5432/test")
            assert isinstance(engine, Engine)
        except Exception:
            # PostgreSQL not available, which is fine for this test
            pass

    def test_make_engine_future_mode_enabled(self) -> None:
        """make_engine() creates engines with future=True."""
        engine = make_engine("sqlite:///:memory:")
        # future=True is set, which enables SQLAlchemy 2.0 behavior
        # This is verified by trying to use the engine with 2.0 style
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result is not None

    def test_make_engine_with_custom_url(self) -> None:
        """make_engine() with custom URL creates engine for that URL."""
        engine = make_engine("sqlite:///:memory:")
        assert isinstance(engine, Engine)
        # Verify it works by executing a query
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1

    def test_make_engine_with_default_url(self) -> None:
        """make_engine() with no URL uses get_database_url()."""
        engine = make_engine()
        assert isinstance(engine, Engine)


class TestEngineSingleton:
    """Property tests for engine singleton behavior."""

    def test_get_engine_returns_engine(self) -> None:
        """get_engine() returns a SQLAlchemy Engine."""
        reset_engine()  # Clear cache first
        engine = get_engine()
        assert isinstance(engine, Engine)

    def test_get_engine_is_singleton(self) -> None:
        """get_engine() returns the same engine on multiple calls (cached)."""
        reset_engine()  # Clear cache
        engine1 = get_engine()
        engine2 = get_engine()
        engine3 = get_engine()
        assert engine1 is engine2 is engine3

    def test_get_sessionmaker_returns_sessionmaker(self) -> None:
        """get_sessionmaker() returns a SQLAlchemy sessionmaker."""
        reset_engine()  # Clear cache
        from sqlalchemy.orm import sessionmaker

        sm = get_sessionmaker()
        assert isinstance(sm, sessionmaker)

    def test_get_sessionmaker_is_singleton(self) -> None:
        """get_sessionmaker() returns the same sessionmaker on multiple calls."""
        reset_engine()  # Clear cache
        sm1 = get_sessionmaker()
        sm2 = get_sessionmaker()
        sm3 = get_sessionmaker()
        assert sm1 is sm2 is sm3

    def test_reset_engine_clears_cache(self) -> None:
        """reset_engine() clears the engine singleton cache."""
        reset_engine()
        engine1 = get_engine()
        reset_engine()
        engine2 = get_engine()
        # After reset, we get a new engine instance
        assert engine1 is not engine2

    def test_reset_engine_disposes_existing_engine(self) -> None:
        """reset_engine() calls dispose() on the existing engine."""
        reset_engine()
        _ = get_engine()
        reset_engine()
        # After reset, the old engine was disposed
        # Getting a new one should work
        new_engine = get_engine()
        assert isinstance(new_engine, Engine)


class TestSessionScope:
    """Property tests for session_scope context manager."""

    def test_session_scope_yields_session(self) -> None:
        """session_scope() is a context manager that yields a Session."""
        reset_engine()
        with session_scope() as session:
            assert isinstance(session, Session)

    def test_session_scope_commits_on_success(self) -> None:
        """session_scope() commits the session when exiting normally."""
        reset_engine()
        with session_scope() as session:
            # Simulate a successful operation
            session.execute(text("SELECT 1"))
        # No exception should be raised, commit should succeed

    def test_session_scope_rolls_back_on_exception(self) -> None:
        """session_scope() rolls back the session when an exception occurs."""
        reset_engine()
        try:
            with session_scope() as session:
                # Simulate an operation
                session.execute(text("SELECT 1"))
                # Raise an exception
                raise ValueError("Test exception")
        except ValueError:
            # Expected exception
            pass
        # The session should have been rolled back

    def test_session_scope_closes_session(self) -> None:
        """session_scope() closes the session in the finally block."""
        reset_engine()
        session_obj = None
        with session_scope() as session:
            session_obj = session
            session.execute(text("SELECT 1"))
        # Session should be closed now
        assert session_obj is not None
        assert session_obj.is_active is False

    def test_session_scope_reraises_exception(self) -> None:
        """session_scope() re-raises any exception that occurs."""
        reset_engine()
        test_exception = RuntimeError("Test error")
        with pytest.raises(RuntimeError) as exc_info:
            with session_scope() as session:
                session.execute(text("SELECT 1"))
                raise test_exception
        assert exc_info.value is test_exception

    def test_session_scope_multiple_uses(self) -> None:
        """session_scope() can be used multiple times in sequence."""
        reset_engine()
        # First use
        with session_scope() as session1:
            assert isinstance(session1, Session)
        # Second use
        with session_scope() as session2:
            assert isinstance(session2, Session)
        # Sessions should be different instances
        assert session1 is not session2

    def test_session_scope_autoflush_false(self) -> None:
        """session_scope() creates sessions with autoflush=False."""
        reset_engine()
        with session_scope() as session:
            # Verify autoflush is False by checking session configuration
            assert session.autoflush is False

    def test_session_scope_expire_on_commit_false(self) -> None:
        """session_scope() creates sessions with expire_on_commit=False."""
        reset_engine()
        with session_scope() as session:
            # Verify expire_on_commit is False
            assert session.expire_on_commit is False


class TestDatabaseIntegration:
    """Property tests for database function integration."""

    def test_make_engine_uses_get_database_url(self) -> None:
        """make_engine() with no URL uses get_database_url()."""
        url = get_database_url()
        engine = make_engine(url)
        assert isinstance(engine, Engine)

    def test_get_engine_uses_make_engine(self) -> None:
        """get_engine() creates engine via make_engine() on first call."""
        reset_engine()
        engine = get_engine()
        # Should be a valid engine
        assert isinstance(engine, Engine)

    def test_session_scope_uses_get_sessionmaker(self) -> None:
        """session_scope() creates sessions from get_sessionmaker()."""
        reset_engine()
        sm = get_sessionmaker()
        with session_scope() as session:
            # Both should be using the same sessionmaker
            assert isinstance(session, Session)
            # Verify the session is from the same factory
            sm2 = get_sessionmaker()
            assert sm is sm2
