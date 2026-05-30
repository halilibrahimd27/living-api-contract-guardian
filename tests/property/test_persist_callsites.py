"""Property-based tests for persist_call_sites function."""

from __future__ import annotations

import pytest
from guardian_core.mining.models import InferredCallSite
from guardian_core.mining.repo_scanner import persist_call_sites
from hypothesis import given
from hypothesis import strategies as st


def _site_strategy() -> st.SearchStrategy[InferredCallSite]:
    """Generate valid InferredCallSite instances."""
    return st.builds(
        InferredCallSite,
        file=st.text(min_size=1, max_size=100, alphabet="abcdefghijklmnopqrstuvwxyz_/."),
        line=st.integers(min_value=1, max_value=10000),
        language=st.sampled_from(["python", "javascript", "typescript"]),
        client_library=st.sampled_from(["requests", "httpx", "fetch", "axios", "grpc"]),
        method=st.sampled_from(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "RPC"]),
        path_template=st.text(min_size=1, max_size=100, alphabet="abcdefghijklmnopqrstuvwxyz_-/{}"),
        fields=st.lists(
            st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz_"), max_size=10
        ),
    )


class TestPersistCallSitesInvariants:
    """Property tests for persist_call_sites core invariants."""

    @pytest.mark.usefixtures("migrated_db")
    def test_empty_sites_returns_zeros(self) -> None:
        """persist_call_sites() with empty list returns zeros."""
        from guardian_core.db import session_scope

        with session_scope() as session:
            result = persist_call_sites(
                session,
                repo="test-repo",
                commit_sha="abc123",
                sites=[],
            )
            assert result.total == 0
            assert result.inserted == 0
            assert result.skipped == 0

    @pytest.mark.usefixtures("migrated_db")
    @given(
        repo=st.text(min_size=1, max_size=100),
        commit_sha=st.text(min_size=1, max_size=100),
    )
    def test_returns_provided_repo_and_sha(self, repo: str, commit_sha: str) -> None:
        """persist_call_sites() returns exactly the provided repo and commit_sha."""
        from guardian_core.db import session_scope

        with session_scope() as session:
            result = persist_call_sites(
                session,
                repo=repo,
                commit_sha=commit_sha,
                sites=[],
            )
            assert result.repo == repo
            assert result.commit_sha == commit_sha

    @pytest.mark.usefixtures("migrated_db")
    @given(sites=st.lists(_site_strategy(), min_size=1, max_size=10))
    def test_total_equals_inserted_plus_skipped(self, sites: list[InferredCallSite]) -> None:
        """persist_call_sites() result.total == inserted + skipped."""
        from guardian_core.db import session_scope

        with session_scope() as session:
            result = persist_call_sites(
                session,
                repo="test-repo",
                commit_sha="abc123",
                sites=sites,
            )
            assert result.total == result.inserted + result.skipped

    @pytest.mark.usefixtures("migrated_db")
    def test_idempotent_second_call_skips_all(self) -> None:
        """persist_call_sites() second call with same sites skips all."""
        from guardian_core.db import session_scope

        site = InferredCallSite(
            file="test.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=["id"],
        )

        with session_scope() as session:
            result1 = persist_call_sites(
                session,
                repo="test-repo",
                commit_sha="abc123",
                sites=[site],
            )
            assert result1.inserted == 1

        with session_scope() as session:
            result2 = persist_call_sites(
                session,
                repo="test-repo",
                commit_sha="abc123",
                sites=[site],
            )
            assert result2.skipped == 1
            assert result2.inserted == 0

    @pytest.mark.usefixtures("migrated_db")
    def test_duplicate_in_batch_skipped(self) -> None:
        """persist_call_sites() skips duplicates within same batch."""
        from guardian_core.db import session_scope

        site = InferredCallSite(
            file="test.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=[],
        )

        with session_scope() as session:
            result = persist_call_sites(
                session,
                repo="test-repo",
                commit_sha="abc123",
                sites=[site, site],
            )
            assert result.inserted == 1
            assert result.skipped == 1
            assert result.total == 2
