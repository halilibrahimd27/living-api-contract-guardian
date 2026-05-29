"""Property-based tests for version and build-info accessors.

Invariants tested:
1. get_version() always returns the constant version string "0.1.0"
2. get_git_sha() returns either an env-provided SHA or "unknown" fallback
3. Both functions return non-empty strings
4. get_git_sha() with GUARDIAN_GIT_SHA set returns exactly that value
"""

from __future__ import annotations

import os

from guardian_core.version import get_git_sha, get_version
from hypothesis import given
from hypothesis import strategies as st


class TestGetVersion:
    """Property tests for get_version()."""

    def test_get_version_returns_constant_version_string(self) -> None:
        """get_version() always returns the constant version "0.1.0"."""
        version = get_version()
        assert version == "0.1.0"
        assert isinstance(version, str)
        assert len(version) > 0

    def test_get_version_is_deterministic(self) -> None:
        """get_version() returns the same value on multiple calls."""
        v1 = get_version()
        v2 = get_version()
        v3 = get_version()
        assert v1 == v2 == v3

    def test_get_version_matches_pyproject_version(self) -> None:
        """get_version() matches the version in pyproject.toml."""
        version = get_version()
        # Parse pyproject.toml to verify version matches
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent.parent
        with open(root / "pyproject.toml") as f:
            for line in f:
                if line.startswith('version = "'):
                    expected_version = line.split('"')[1]
                    assert version == expected_version
                    break


class TestGetGitSha:
    """Property tests for get_git_sha()."""

    def test_get_git_sha_returns_non_empty_string(self) -> None:
        """get_git_sha() always returns a non-empty string."""
        sha = get_git_sha()
        assert isinstance(sha, str)
        assert len(sha) > 0

    def test_get_git_sha_is_deterministic(self) -> None:
        """get_git_sha() returns the same value on multiple calls."""
        sha1 = get_git_sha()
        sha2 = get_git_sha()
        sha3 = get_git_sha()
        assert sha1 == sha2 == sha3

    def test_get_git_sha_fallback_to_unknown(self) -> None:
        """get_git_sha() returns 'unknown' when GUARDIAN_GIT_SHA is not set."""
        # Save original env
        original = os.environ.pop("GUARDIAN_GIT_SHA", None)
        try:
            sha = get_git_sha()
            assert sha == "unknown"
        finally:
            # Restore original env
            if original is not None:
                os.environ["GUARDIAN_GIT_SHA"] = original

    @given(sha_value=st.text(min_size=1, max_size=100))
    def test_get_git_sha_returns_env_value_when_set(self, sha_value: str) -> None:
        """get_git_sha() returns GUARDIAN_GIT_SHA when environment variable is set."""
        # Save original env
        original = os.environ.get("GUARDIAN_GIT_SHA")
        try:
            os.environ["GUARDIAN_GIT_SHA"] = sha_value
            # Force reimport to pick up new env var
            # (Note: this works because the function reads env on each call)
            result = get_git_sha()
            assert result == sha_value
        finally:
            # Restore original env
            if original is not None:
                os.environ["GUARDIAN_GIT_SHA"] = original
            else:
                os.environ.pop("GUARDIAN_GIT_SHA", None)


class TestVersionAuxiliaries:
    """Property tests for version function characteristics."""

    def test_version_string_is_semantic_version(self) -> None:
        """Version string follows semantic versioning format (major.minor.patch)."""
        version = get_version()
        parts = version.split(".")
        assert len(parts) == 3
        # Each part should be numeric
        for part in parts:
            assert part.isdigit()

    def test_git_sha_or_unknown(self) -> None:
        """get_git_sha() returns either a non-empty string or 'unknown'."""
        sha = get_git_sha()
        assert sha == "unknown" or (isinstance(sha, str) and len(sha) > 0)

    def test_version_and_sha_are_strings(self) -> None:
        """Both version and git_sha functions return strings."""
        version = get_version()
        sha = get_git_sha()
        assert isinstance(version, str)
        assert isinstance(sha, str)
