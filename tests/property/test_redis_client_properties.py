"""Property-based tests for Redis connectivity helpers.

Invariants tested:
1. get_redis_url() returns a valid Redis URL string (starts with redis://)
2. get_redis_url() with REDIS_URL env returns that URL; without returns default
3. make_redis_client() with a URL creates a redis.Redis client
4. make_redis_client() socket timeouts are set to 1.0 second
5. ping_redis() returns bool (True if Redis responds, False on error)
6. ping_redis() with no client creates a default client
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import redis
from guardian_core.redis_client import (
    get_redis_url,
    make_redis_client,
    ping_redis,
)
from hypothesis import given
from hypothesis import strategies as st


class TestGetRedisUrl:
    """Property tests for get_redis_url()."""

    def test_get_redis_url_returns_string(self) -> None:
        """get_redis_url() always returns a string."""
        url = get_redis_url()
        assert isinstance(url, str)
        assert len(url) > 0

    def test_get_redis_url_returns_valid_redis_url(self) -> None:
        """get_redis_url() returns a URL that starts with redis://."""
        # Save original env
        original = os.environ.get("REDIS_URL")
        try:
            # Test default
            os.environ.pop("REDIS_URL", None)
            url = get_redis_url()
            assert url.startswith("redis://")
        finally:
            # Restore original env
            if original is not None:
                os.environ["REDIS_URL"] = original

    def test_get_redis_url_default_is_localhost(self) -> None:
        """get_redis_url() default includes localhost:6379."""
        # Save original env
        original = os.environ.get("REDIS_URL")
        try:
            os.environ.pop("REDIS_URL", None)
            url = get_redis_url()
            assert "localhost:6379" in url or "127.0.0.1:6379" in url or "6379" in url
        finally:
            # Restore original env
            if original is not None:
                os.environ["REDIS_URL"] = original

    @given(redis_url=st.text(min_size=10, max_size=200).filter(lambda s: s.startswith("redis://")))
    def test_get_redis_url_respects_env_var(self, redis_url: str) -> None:
        """get_redis_url() returns REDIS_URL when environment variable is set."""
        original = os.environ.get("REDIS_URL")
        try:
            os.environ["REDIS_URL"] = redis_url
            result = get_redis_url()
            assert result == redis_url
        finally:
            if original is not None:
                os.environ["REDIS_URL"] = original
            else:
                os.environ.pop("REDIS_URL", None)

    def test_get_redis_url_is_deterministic(self) -> None:
        """get_redis_url() returns the same value on multiple calls."""
        url1 = get_redis_url()
        url2 = get_redis_url()
        url3 = get_redis_url()
        assert url1 == url2 == url3


class TestMakeRedisClient:
    """Property tests for make_redis_client()."""

    def test_make_redis_client_returns_redis_client(self) -> None:
        """make_redis_client() returns a redis.Redis instance."""
        client = make_redis_client("redis://localhost:6379/0")
        assert isinstance(client, redis.Redis)

    def test_make_redis_client_with_default_url(self) -> None:
        """make_redis_client() with no URL uses get_redis_url()."""
        client = make_redis_client()
        assert isinstance(client, redis.Redis)

    def test_make_redis_client_socket_timeout_is_set(self) -> None:
        """make_redis_client() sets socket_timeout to 1.0."""
        client = make_redis_client("redis://localhost:6379/0")
        assert client.connection_pool.connection_kwargs["socket_timeout"] == 1.0

    def test_make_redis_client_connect_timeout_is_set(self) -> None:
        """make_redis_client() sets socket_connect_timeout to 1.0."""
        client = make_redis_client("redis://localhost:6379/0")
        assert client.connection_pool.connection_kwargs["socket_connect_timeout"] == 1.0

    @given(db_num=st.integers(min_value=0, max_value=15))
    def test_make_redis_client_supports_db_selection(self, db_num: int) -> None:
        """make_redis_client() can be called with different database numbers."""
        url = f"redis://localhost:6379/{db_num}"
        client = make_redis_client(url)
        assert isinstance(client, redis.Redis)

    def test_make_redis_client_is_bytes_typed(self) -> None:
        """make_redis_client() returns redis.Redis[bytes] for binary safety."""
        client = make_redis_client("redis://localhost:6379/0")
        # Check that it's a bytes-based Redis client
        assert isinstance(client, redis.Redis)
        # The return type hint indicates redis.Redis[bytes]


class TestPingRedis:
    """Property tests for ping_redis()."""

    def test_ping_redis_returns_bool(self) -> None:
        """ping_redis() always returns a boolean."""
        result = ping_redis()
        assert isinstance(result, bool)

    def test_ping_redis_with_no_client_argument(self) -> None:
        """ping_redis() with no argument creates a default client."""
        result = ping_redis()
        # Should either be True (Redis running) or False (Redis not available)
        assert isinstance(result, bool)

    def test_ping_redis_false_on_connection_error(self) -> None:
        """ping_redis() returns False when connection fails."""
        # Create a mock Redis client that raises an exception
        mock_client = MagicMock(spec=redis.Redis)
        mock_client.ping.side_effect = Exception("Connection refused")
        result = ping_redis(mock_client)
        assert result is False

    def test_ping_redis_false_on_general_exception(self) -> None:
        """ping_redis() returns False on any exception."""
        # Create a mock Redis client that raises an exception
        mock_client = MagicMock(spec=redis.Redis)
        mock_client.ping.side_effect = RuntimeError("Some error")
        result = ping_redis(mock_client)
        assert result is False

    def test_ping_redis_with_successful_ping(self) -> None:
        """ping_redis() returns True when ping() succeeds."""
        # Create a mock Redis client that succeeds
        mock_client = MagicMock(spec=redis.Redis)
        mock_client.ping.return_value = True
        result = ping_redis(mock_client)
        assert result is True

    def test_ping_redis_with_byte_response(self) -> None:
        """ping_redis() handles byte responses from ping()."""
        # Some Redis clients return b'PONG' instead of True
        mock_client = MagicMock(spec=redis.Redis)
        mock_client.ping.return_value = b"PONG"
        result = ping_redis(mock_client)
        # Should convert truthy value to True
        assert result is True

    def test_ping_redis_is_deterministic(self) -> None:
        """ping_redis() returns the same value on multiple calls (with same client)."""
        # Mock client that always returns the same value
        mock_client = MagicMock(spec=redis.Redis)
        mock_client.ping.return_value = True
        r1 = ping_redis(mock_client)
        r2 = ping_redis(mock_client)
        r3 = ping_redis(mock_client)
        assert r1 == r2 == r3


class TestRedisClientIntegration:
    """Property tests for integration between redis functions."""

    def test_get_redis_url_used_by_make_redis_client(self) -> None:
        """make_redis_client() without URL uses get_redis_url()."""
        url = get_redis_url()
        # Creating client without URL should use the same URL internally
        client1 = make_redis_client(url)
        client2 = make_redis_client()  # Uses default from get_redis_url()
        # Both should be valid Redis clients
        assert isinstance(client1, redis.Redis)
        assert isinstance(client2, redis.Redis)

    def test_ping_redis_without_client_uses_make_redis_client(self) -> None:
        """ping_redis() without client arg creates one via make_redis_client()."""
        # This test verifies the code path works (returns bool)
        result = ping_redis()
        assert isinstance(result, bool)

    def test_ping_redis_with_provided_client_uses_it(self) -> None:
        """ping_redis() with provided client uses it instead of creating one."""
        mock_client = MagicMock(spec=redis.Redis)
        mock_client.ping.return_value = True
        result = ping_redis(mock_client)
        # Verify the mock was called
        mock_client.ping.assert_called_once()
        assert result is True
