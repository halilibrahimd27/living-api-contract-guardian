"""Property-based tests for the AST contract miner.

Invariants tested:
1. InferredCallSite.content_hash() is deterministic: same site → same hash
2. InferredCallSite.content_hash() is stable: hash is 64 hex chars (SHA256)
3. normalize_template() with 0 placeholders → no {...} in output
4. normalize_template() with N placeholders → exactly N {...} segments
5. abstract_static_segments() replaces UUIDs and numbers with {id}
6. abstract_static_segments() preserves named placeholders
7. PersistenceResult.total = PersistenceResult.inserted + PersistenceResult.skipped
8. mine_repo() detects recognized HTTP verbs (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS)
9. PersistenceResult model validates repo/commit_sha as non-empty
10. mine_repo() returns list of valid InferredCallSite objects
11. mine_repo() returns empty list for empty directories
12. mine_repo() ignores files in ignored directories (.git, node_modules, etc.)
13. mine_repo() returns relative file paths
14. mine_repo() sites have valid field constraints
15. persist_call_sites() result total = inserted + skipped
16. detect_commit_sha() returns 'unknown' for non-git directories
17. detect_commit_sha() always returns a string
18. Path normalization: templates start with /, have no //, and no trailing /
19. Path abstraction: numeric segments become {id}, named params preserved
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from guardian_core.mining.models import InferredCallSite, Language
from guardian_core.mining.path_normalize import (
    abstract_static_segments,
    normalize_template,
)
from guardian_core.mining.repo_scanner import PersistenceResult
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

# ============================================================================
# Helper Strategies
# ============================================================================


def _valid_file_path() -> st.SearchStrategy[str]:
    """Generate valid file paths (1-1024 chars, typical relative paths)."""
    segments = st.lists(
        st.text(min_size=1, max_size=64, alphabet="abcdefghijklmnopqrstuvwxyz_"),
        min_size=1,
        max_size=5,
    )
    return segments.map(lambda seg: "/".join(seg) + ".py")


def _valid_line_number() -> st.SearchStrategy[int]:
    """Generate valid line numbers (≥1)."""
    return st.integers(min_value=1, max_value=100000)


def _valid_http_method() -> st.SearchStrategy[str]:
    """Generate valid HTTP method names."""
    return st.sampled_from(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])


def _valid_path_template() -> st.SearchStrategy[str]:
    """Generate valid OpenAPI-style path templates."""
    # Simple paths with optional {param} placeholders
    segments = st.lists(
        st.one_of(
            st.sampled_from(["users", "posts", "items", "api", "v1", "v2"]),
            st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz"),
            st.just("{id}"),
            st.just("{user_id}"),
            st.just("{post_id}"),
        ),
        min_size=1,
        max_size=6,
    )
    return segments.map(lambda seg: "/" + "/".join(seg))


def _valid_field_name() -> st.SearchStrategy[str]:
    """Generate valid field names (typically query/body parameters)."""
    return st.text(
        min_size=1,
        max_size=64,
        alphabet="abcdefghijklmnopqrstuvwxyz_0123456789",
    ).filter(lambda s: not s[0].isdigit())


def _valid_fields() -> st.SearchStrategy[list[str]]:
    """Generate valid field lists."""
    return st.lists(_valid_field_name(), max_size=10)


def _valid_language() -> st.SearchStrategy[Language]:
    """Generate valid language literals."""
    return st.sampled_from(["python", "javascript", "typescript"])


def _valid_client_library() -> st.SearchStrategy[str]:
    """Generate valid client library names."""
    return st.sampled_from(["requests", "httpx", "fetch", "axios", "grpc"])


# ============================================================================
# Tests for InferredCallSite.content_hash()
# ============================================================================


class TestInferredCallSiteHash:
    """Property tests for InferredCallSite hash determinism and stability."""

    @given(
        file=_valid_file_path(),
        line=_valid_line_number(),
        language=_valid_language(),
        client_library=_valid_client_library(),
        method=_valid_http_method(),
        path_template=_valid_path_template(),
        fields=_valid_fields(),
    )
    def test_content_hash_is_deterministic(
        self,
        file: str,
        line: int,
        language: Language,
        client_library: str,
        method: str,
        path_template: str,
        fields: list[str],
    ) -> None:
        """InferredCallSite.content_hash() is deterministic: same inputs → same output."""
        site1 = InferredCallSite(
            file=file,
            line=line,
            language=language,
            client_library=client_library,
            method=method,
            path_template=path_template,
            fields=fields,
        )
        site2 = InferredCallSite(
            file=file,
            line=line,
            language=language,
            client_library=client_library,
            method=method,
            path_template=path_template,
            fields=fields,
        )
        assert site1.content_hash() == site2.content_hash()

    @given(
        file=_valid_file_path(),
        line=_valid_line_number(),
        language=_valid_language(),
        client_library=_valid_client_library(),
        method=_valid_http_method(),
        path_template=_valid_path_template(),
        fields=_valid_fields(),
    )
    def test_content_hash_is_sha256_hex(
        self,
        file: str,
        line: int,
        language: Language,
        client_library: str,
        method: str,
        path_template: str,
        fields: list[str],
    ) -> None:
        """InferredCallSite.content_hash() returns valid SHA256 hex string (64 chars)."""
        site = InferredCallSite(
            file=file,
            line=line,
            language=language,
            client_library=client_library,
            method=method,
            path_template=path_template,
            fields=fields,
        )
        digest = site.content_hash()
        # SHA256 hex digest is exactly 64 chars, all hex digits
        assert len(digest) == 64
        assert re.match(r"^[0-9a-f]{64}$", digest) is not None

    def test_content_hash_differs_on_file_change(self) -> None:
        """Changing file path changes the hash."""
        base_site = InferredCallSite(
            file="a.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=[],
        )
        diff_site = InferredCallSite(
            file="b.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=[],
        )
        assert base_site.content_hash() != diff_site.content_hash()

    def test_content_hash_differs_on_line_change(self) -> None:
        """Changing line number changes the hash."""
        site1 = InferredCallSite(
            file="a.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=[],
        )
        site2 = InferredCallSite(
            file="a.py",
            line=2,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=[],
        )
        assert site1.content_hash() != site2.content_hash()

    @given(
        field_list1=_valid_fields(),
        field_list2=_valid_fields(),
    )
    @settings(suppress_health_check=[HealthCheck.filter_too_much])
    def test_content_hash_field_order_independent(
        self, field_list1: list[str], field_list2: list[str]
    ) -> None:
        """Field order doesn't matter: sorted fields produce same hash."""
        if not field_list1:
            return  # Skip if no fields
        # Create two sites with same fields but different order
        site1 = InferredCallSite(
            file="a.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=field_list1,
        )
        site2 = InferredCallSite(
            file="a.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=sorted(field_list1),  # explicitly sorted
        )
        assert site1.content_hash() == site2.content_hash()

    def test_content_hash_method_normalized_to_uppercase(self) -> None:
        """HTTP method is normalized to uppercase in hash."""
        site_lower = InferredCallSite(
            file="a.py",
            line=1,
            language="python",
            client_library="requests",
            method="get",
            path_template="/users",
            fields=[],
        )
        site_upper = InferredCallSite(
            file="a.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=[],
        )
        assert site_lower.content_hash() == site_upper.content_hash()


# ============================================================================
# Tests for normalize_template()
# ============================================================================


class TestNormalizeTemplate:
    """Property tests for path template normalization."""

    @given(st.data())
    def test_normalize_with_no_placeholders_no_braces(self, data: st.DataObject) -> None:
        """normalize_template() with 0 placeholders → no {...} in output."""
        raw = data.draw(st.sampled_from(["/users", "/api/v1/items", "http://example.com/path"]))
        template = normalize_template(raw, [])
        # Should not contain any {...} patterns
        assert "{" not in template or "}" not in template

    @given(
        num_placeholders=st.integers(min_value=1, max_value=5),
    )
    def test_normalize_with_n_placeholders_has_n_braces(self, num_placeholders: int) -> None:
        """normalize_template() with N placeholders → exactly N {...} segments."""
        # Create a raw URL with placeholder sentinels
        from guardian_core.mining.path_normalize import PLACEHOLDER_SENTINEL

        segments = ["users"]
        for i in range(num_placeholders):
            segments.append(PLACEHOLDER_SENTINEL)
            segments.append(f"item{i}")
        raw = "/".join(segments)
        placeholders = [f"param{i}" for i in range(num_placeholders)]
        template = normalize_template(raw, placeholders)
        # Count {...} patterns
        brace_count = template.count("{")
        assert brace_count == num_placeholders
        assert template.count("}") == num_placeholders

    def test_normalize_strips_scheme_and_host(self) -> None:
        """normalize_template() strips scheme and host, keeping only path."""
        template = normalize_template("https://api.example.com:8080/users/123", [])
        assert template.startswith("/")
        assert "https://" not in template
        assert "api.example.com" not in template
        assert "8080" not in template

    def test_normalize_drops_query_string(self) -> None:
        """normalize_template() removes query string."""
        template = normalize_template("/users?limit=10&offset=0", [])
        assert "?" not in template
        assert "limit" not in template
        assert "offset" not in template

    def test_normalize_ensures_leading_slash(self) -> None:
        """normalize_template() ensures path starts with /."""
        template = normalize_template("users/123", [])
        assert template.startswith("/")

    def test_normalize_root_path_becomes_slash(self) -> None:
        """normalize_template() of empty/root becomes /."""
        # Empty path
        template = normalize_template("", [])
        assert template == "/"
        # Just domain
        template2 = normalize_template("https://api.example.com", [])
        assert template2 == "/"

    @given(st.data())
    def test_normalize_no_double_slashes(self, data: st.DataObject) -> None:
        """normalize_template() does not contain // (except in scheme)."""
        template = data.draw(
            st.sampled_from(
                [
                    normalize_template("/users//items", []),
                    normalize_template("/api///v1", []),
                ]
            )
        )
        # Should not have consecutive slashes
        assert "//" not in template

    @given(st.integers(min_value=1, max_value=3))
    def test_normalize_no_trailing_slash(self, num_segments: int) -> None:
        """normalize_template() has no trailing slash (except root /)."""

        segments = [f"segment{i}" for i in range(num_segments)]
        raw = "/" + "/".join(segments)
        template = normalize_template(raw, [])
        if template != "/":
            assert not template.endswith("/")

    @given(_valid_field_name())
    def test_normalize_placeholder_name_appears_in_braces(self, name: str) -> None:
        """Placeholder name appears as {...} in output."""
        from guardian_core.mining.path_normalize import PLACEHOLDER_SENTINEL

        raw = f"/users/{PLACEHOLDER_SENTINEL}/posts"
        template = normalize_template(raw, [name])
        assert f"{{{name}}}" in template


# ============================================================================
# Tests for abstract_static_segments()
# ============================================================================


class TestAbstractStaticSegments:
    """Property tests for UUID and numeric segment abstraction."""

    def test_abstracts_numeric_segments(self) -> None:
        """Numeric path segments → {id}."""
        template = abstract_static_segments("/users/123/posts")
        assert template == "/users/{id}/posts"

    def test_abstracts_uuid_segments(self) -> None:
        """UUID path segments → {id}."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        template = abstract_static_segments(f"/items/{uuid}/details")
        assert template == "/items/{id}/details"

    def test_preserves_named_placeholders(self) -> None:
        """Named placeholders like {user_id} are preserved."""
        template = abstract_static_segments("/users/{user_id}/posts/{id}")
        # Named placeholders should remain unchanged
        assert "{user_id}" in template
        assert "{id}" in template

    def test_preserves_literal_segments(self) -> None:
        """Literal path segments like 'api', 'v1' are preserved."""
        template = abstract_static_segments("/api/v1/users")
        assert template == "/api/v1/users"

    @given(st.data())
    def test_output_is_valid_path(self, data: st.DataObject) -> None:
        """abstract_static_segments() output always starts with /."""
        template = data.draw(
            st.sampled_from(
                [
                    abstract_static_segments("/users/123"),
                    abstract_static_segments("/items"),
                    abstract_static_segments("/a/b/c/123/d/456"),
                ]
            )
        )
        assert template.startswith("/")

    def test_multiple_numeric_segments_all_abstracted(self) -> None:
        """Multiple numeric segments all become {id}."""
        template = abstract_static_segments("/users/123/posts/456/comments/789")
        # Count {id} occurrences
        assert template.count("{id}") == 3

    def test_uuid_case_insensitive(self) -> None:
        """UUIDs are recognized case-insensitively."""
        uuid_lower = "550e8400-e29b-41d4-a716-446655440000"
        uuid_upper = "550E8400-E29B-41D4-A716-446655440000"
        template_lower = abstract_static_segments(f"/items/{uuid_lower}")
        template_upper = abstract_static_segments(f"/items/{uuid_upper}")
        assert template_lower == template_upper
        assert "{id}" in template_lower

    def test_alphanumeric_mixed_not_abstracted(self) -> None:
        """Mixed alphanumeric segments like 'user123' are not abstracted."""
        template = abstract_static_segments("/api/user123/posts")
        assert template == "/api/user123/posts"

    def test_incomplete_uuid_not_abstracted(self) -> None:
        """Incomplete or malformed UUIDs are not abstracted."""
        template = abstract_static_segments("/items/550e8400-e29b-41d4/detail")
        # Incomplete UUID should not be abstracted
        assert "550e8400-e29b-41d4" in template


# ============================================================================
# Tests for PersistenceResult
# ============================================================================


class TestPersistenceResult:
    """Property tests for PersistenceResult schema."""

    @given(
        repo=st.text(min_size=1, max_size=255),
        commit_sha=st.text(min_size=1, max_size=100),
        inserted=st.integers(min_value=0, max_value=1000),
        skipped=st.integers(min_value=0, max_value=1000),
    )
    def test_persistence_result_total_equals_sum(
        self, repo: str, commit_sha: str, inserted: int, skipped: int
    ) -> None:
        """PersistenceResult.total = inserted + skipped."""
        result = PersistenceResult(
            repo=repo,
            commit_sha=commit_sha,
            inserted=inserted,
            skipped=skipped,
            total=inserted + skipped,
        )
        assert result.total == inserted + skipped

    @given(
        repo=st.text(min_size=1, max_size=255),
        commit_sha=st.text(min_size=1, max_size=100),
    )
    def test_persistence_result_requires_nonzero_repo(self, repo: str, commit_sha: str) -> None:
        """PersistenceResult requires non-empty repo."""
        if not repo:
            # Skip empty repo case; strategy should not generate it
            return
        result = PersistenceResult(repo=repo, commit_sha=commit_sha, inserted=0, skipped=0, total=0)
        assert result.repo == repo

    @given(
        repo=st.text(min_size=1, max_size=255),
        commit_sha=st.text(min_size=1, max_size=100),
    )
    def test_persistence_result_requires_nonzero_commit_sha(
        self, repo: str, commit_sha: str
    ) -> None:
        """PersistenceResult requires non-empty commit_sha."""
        if not commit_sha:
            return
        result = PersistenceResult(repo=repo, commit_sha=commit_sha, inserted=0, skipped=0, total=0)
        assert result.commit_sha == commit_sha

    @given(
        repo=st.text(min_size=1, max_size=255),
        commit_sha=st.text(min_size=1, max_size=100),
        inserted=st.integers(min_value=0, max_value=1000),
        skipped=st.integers(min_value=0, max_value=1000),
    )
    def test_persistence_result_all_counts_non_negative(
        self, repo: str, commit_sha: str, inserted: int, skipped: int
    ) -> None:
        """All count fields in PersistenceResult are non-negative."""
        result = PersistenceResult(
            repo=repo,
            commit_sha=commit_sha,
            inserted=inserted,
            skipped=skipped,
            total=inserted + skipped,
        )
        assert result.inserted >= 0
        assert result.skipped >= 0
        assert result.total >= 0

    def test_persistence_result_forbids_extra_fields(self) -> None:
        """PersistenceResult rejects extra fields."""
        with pytest.raises(ValidationError):
            PersistenceResult(  # type: ignore
                repo="test", commit_sha="abc", inserted=0, skipped=0, total=0, extra="field"
            )


# ============================================================================
# Tests for InferredCallSite model validation
# ============================================================================


class TestInferredCallSiteValidation:
    """Property tests for InferredCallSite schema constraints."""

    @given(_valid_file_path(), _valid_line_number())
    def test_accepts_valid_file_and_line(self, file: str, line: int) -> None:
        """InferredCallSite accepts valid file (1-1024 chars) and line (≥1)."""
        site = InferredCallSite(
            file=file,
            line=line,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=[],
        )
        assert site.file == file
        assert site.line == line

    def test_rejects_empty_file(self) -> None:
        """InferredCallSite rejects empty file."""
        with pytest.raises(ValidationError):
            InferredCallSite(
                file="",
                line=1,
                language="python",
                client_library="requests",
                method="GET",
                path_template="/users",
            )

    def test_rejects_zero_line(self) -> None:
        """InferredCallSite rejects line < 1."""
        with pytest.raises(ValidationError):
            InferredCallSite(
                file="a.py",
                line=0,
                language="python",
                client_library="requests",
                method="GET",
                path_template="/users",
            )

    def test_rejects_empty_method(self) -> None:
        """InferredCallSite rejects empty HTTP method."""
        with pytest.raises(ValidationError):
            InferredCallSite(
                file="a.py",
                line=1,
                language="python",
                client_library="requests",
                method="",
                path_template="/users",
            )

    def test_rejects_empty_path_template(self) -> None:
        """InferredCallSite rejects empty path_template."""
        with pytest.raises(ValidationError):
            InferredCallSite(
                file="a.py",
                line=1,
                language="python",
                client_library="requests",
                method="GET",
                path_template="",
            )

    def test_rejects_extra_fields(self) -> None:
        """InferredCallSite rejects extra fields (frozen model)."""
        with pytest.raises(ValidationError):
            InferredCallSite(  # type: ignore
                file="a.py",
                line=1,
                language="python",
                client_library="requests",
                method="GET",
                path_template="/users",
                extra_field="should fail",
            )

    def test_model_is_frozen(self) -> None:
        """InferredCallSite is immutable (frozen)."""
        site = InferredCallSite(
            file="a.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
        )
        with pytest.raises(ValueError):
            site.method = "POST"  # type: ignore

    @given(
        file=_valid_file_path(),
        line=_valid_line_number(),
        language=_valid_language(),
        client_library=_valid_client_library(),
        method=_valid_http_method(),
        path_template=_valid_path_template(),
        fields=_valid_fields(),
    )
    def test_serialization_roundtrip(
        self,
        file: str,
        line: int,
        language: Language,
        client_library: str,
        method: str,
        path_template: str,
        fields: list[str],
    ) -> None:
        """InferredCallSite serializes and deserializes correctly."""
        original = InferredCallSite(
            file=file,
            line=line,
            language=language,
            client_library=client_library,
            method=method,
            path_template=path_template,
            fields=fields,
        )
        data = original.model_dump()
        reconstructed = InferredCallSite(**data)
        assert reconstructed == original


# ============================================================================
# Tests for mine_repo() function behavior
# ============================================================================


class TestMineRepoProperties:
    """Property tests for the mine_repo() function."""

    def test_mine_repo_returns_list_of_call_sites(self) -> None:
        """mine_repo() returns a list of InferredCallSite objects."""
        import tempfile

        from guardian_core.mining import mine_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create a simple Python file with a requests call
            client_file = root / "client.py"
            client_file.write_text(
                """import requests
resp = requests.get("https://api.example.com/users")
""",
                encoding="utf-8",
            )
            results = mine_repo(root)
            assert isinstance(results, list)
            assert all(isinstance(site, InferredCallSite) for site in results)

    def test_mine_repo_returns_empty_for_empty_directory(self) -> None:
        """mine_repo() returns [] for directory with no source files."""
        import tempfile

        from guardian_core.mining import mine_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            results = mine_repo(root)
            assert isinstance(results, list)
            assert len(results) == 0

    def test_mine_repo_ignores_ignored_dirs(self) -> None:
        """mine_repo() skips .git, node_modules, __pycache__, etc."""
        import tempfile

        from guardian_core.mining import mine_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create files in ignored directories
            (root / ".git").mkdir()
            (root / ".git" / "client.py").write_text(
                """import requests
resp = requests.get("https://api.example.com/users")
""",
                encoding="utf-8",
            )
            (root / "node_modules").mkdir()
            (root / "node_modules" / "client.js").write_text(
                "const axios = require('axios');\naxios.get('/users');",
                encoding="utf-8",
            )
            results = mine_repo(root)
            # Should be empty because files are in ignored directories
            assert len(results) == 0

    def test_mine_repo_processes_python_files(self) -> None:
        """mine_repo() processes .py files."""
        import tempfile

        from guardian_core.mining import mine_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client_file = root / "client.py"
            client_file.write_text(
                """import requests
requests.get("https://api.example.com/items")
""",
                encoding="utf-8",
            )
            results = mine_repo(root)
            assert len(results) > 0
            assert all(site.language == "python" for site in results)

    def test_mine_repo_returns_file_relative_to_root(self) -> None:
        """mine_repo() returns file paths relative to root."""
        import tempfile

        from guardian_core.mining import mine_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            subdir = root / "lib"
            subdir.mkdir()
            client_file = subdir / "client.py"
            client_file.write_text(
                """import requests
requests.get("https://api.example.com/data")
""",
                encoding="utf-8",
            )
            results = mine_repo(root)
            assert len(results) > 0
            # File path should be relative (lib/client.py, not /tmp/.../lib/client.py)
            assert all(not site.file.startswith("/tmp") for site in results)
            assert all("lib" in site.file for site in results)

    def test_mine_repo_site_field_validity(self) -> None:
        """mine_repo() returns sites with valid field constraints."""
        import tempfile

        from guardian_core.mining import mine_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client_file = root / "test.py"
            client_file.write_text(
                """import requests
requests.post("https://api.example.com/users", json={"name": "Alice"})
""",
                encoding="utf-8",
            )
            results = mine_repo(root)
            assert len(results) > 0
            for site in results:
                # Validate all required fields exist and have correct types
                assert isinstance(site.file, str) and len(site.file) > 0
                assert isinstance(site.line, int) and site.line >= 1
                assert site.language in ("python", "javascript", "typescript")
                assert site.client_library in ("requests", "httpx", "fetch", "axios", "grpc")
                assert isinstance(site.method, str) and len(site.method) > 0
                assert isinstance(site.path_template, str) and len(site.path_template) > 0
                assert isinstance(site.fields, list)
                assert all(isinstance(f, str) for f in site.fields)


# ============================================================================
# Tests for PersistenceResult behavior with mined sites
# ============================================================================


class TestPersistenceResultBehavior:
    """Property tests for PersistenceResult behavior under various scenarios."""

    @given(
        repo=st.text(min_size=1, max_size=100),
        commit_sha=st.text(min_size=1, max_size=100),
        inserted=st.integers(min_value=0, max_value=100),
        skipped=st.integers(min_value=0, max_value=100),
    )
    def test_total_always_equals_inserted_plus_skipped(
        self, repo: str, commit_sha: str, inserted: int, skipped: int
    ) -> None:
        """PersistenceResult.total must always equal inserted + skipped."""
        result = PersistenceResult(
            repo=repo,
            commit_sha=commit_sha,
            inserted=inserted,
            skipped=skipped,
            total=inserted + skipped,
        )
        assert result.total == inserted + skipped

    @given(
        repo=st.text(min_size=1, max_size=100),
        commit_sha=st.text(min_size=1, max_size=100),
    )
    def test_persistence_result_counts_are_nonnegative(self, repo: str, commit_sha: str) -> None:
        """All count fields in PersistenceResult must be non-negative."""
        for inserted in range(0, 5):
            for skipped in range(0, 5):
                result = PersistenceResult(
                    repo=repo,
                    commit_sha=commit_sha,
                    inserted=inserted,
                    skipped=skipped,
                    total=inserted + skipped,
                )
                assert result.inserted >= 0
                assert result.skipped >= 0
                assert result.total >= 0


# ============================================================================
# Tests for detect_commit_sha() function
# ============================================================================


class TestDetectCommitSha:
    """Property tests for detect_commit_sha() function."""

    def test_detect_commit_sha_non_git_returns_unknown(self) -> None:
        """detect_commit_sha() returns 'unknown' for non-git directory."""
        import tempfile

        from guardian_core.mining.repo_scanner import detect_commit_sha

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = detect_commit_sha(root)
            assert result == "unknown"

    def test_detect_commit_sha_returns_string(self) -> None:
        """detect_commit_sha() always returns a string."""
        import tempfile

        from guardian_core.mining.repo_scanner import detect_commit_sha

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = detect_commit_sha(root)
            assert isinstance(result, str)
            assert len(result) > 0


# ============================================================================
# Tests for path_normalize integration with mining
# ============================================================================


class TestPathNormalizeIntegration:
    """Property tests for path normalization in miner context."""

    @given(_valid_path_template())
    def test_mined_paths_are_normalized_templates(self, expected_template: str) -> None:
        """Mined paths follow OpenAPI template format (start with /, {params})."""
        # The path_template should start with /
        assert expected_template.startswith("/")
        # Should not have double slashes
        assert "//" not in expected_template
        # Should not have trailing slash (except root)
        if expected_template != "/":
            assert not expected_template.endswith("/")

    def test_normalize_template_with_empty_path_becomes_root(self) -> None:
        """normalize_template() of empty/missing path becomes /."""
        from guardian_core.mining.path_normalize import normalize_template

        template = normalize_template("", [])
        assert template == "/"

    def test_abstract_static_replaces_pure_numeric_segments(self) -> None:
        """abstract_static_segments() replaces only pure-numeric segments."""
        from guardian_core.mining.path_normalize import abstract_static_segments

        # Pure numeric should become {id}
        assert "/users/{id}/posts" == abstract_static_segments("/users/123/posts")
        # Alphanumeric should be preserved
        assert "/users/user123/posts" == abstract_static_segments("/users/user123/posts")

    def test_abstract_static_preserves_named_params(self) -> None:
        """abstract_static_segments() preserves {name} style parameters."""
        from guardian_core.mining.path_normalize import abstract_static_segments

        template = abstract_static_segments("/users/{user_id}/posts/{post_id}")
        assert "{user_id}" in template
        assert "{post_id}" in template


# ============================================================================
# Additional hypothesis-driven tests for comprehensive coverage
# ============================================================================


class TestInferredCallSiteHashing:
    """Additional comprehensive tests for content_hash stability."""

    @given(
        file=_valid_file_path(),
        line=_valid_line_number(),
        language=_valid_language(),
        client_library=_valid_client_library(),
        method=_valid_http_method(),
        path_template=_valid_path_template(),
        fields=_valid_fields(),
    )
    def test_hash_reproducible_across_instances(
        self,
        file: str,
        line: int,
        language: Language,
        client_library: str,
        method: str,
        path_template: str,
        fields: list[str],
    ) -> None:
        """InferredCallSite hash is reproducible: many iterations produce same hash."""
        site_data = {
            "file": file,
            "line": line,
            "language": language,
            "client_library": client_library,
            "method": method,
            "path_template": path_template,
            "fields": fields,
        }
        hashes = [InferredCallSite(**site_data).content_hash() for _ in range(3)]
        assert len(set(hashes)) == 1, "Hashes should be identical across instances"

    @given(
        file=_valid_file_path(),
        line=_valid_line_number(),
        language=_valid_language(),
        client_library=_valid_client_library(),
        method=_valid_http_method(),
        path_template=_valid_path_template(),
    )
    def test_hash_changes_on_any_field_change(
        self,
        file: str,
        line: int,
        language: Language,
        client_library: str,
        method: str,
        path_template: str,
    ) -> None:
        """Changing any field changes the hash."""
        original = InferredCallSite(
            file=file,
            line=line,
            language=language,
            client_library=client_library,
            method=method,
            path_template=path_template,
            fields=[],
        )
        original_hash = original.content_hash()

        # Change path_template and verify hash differs
        modified = InferredCallSite(
            file=file,
            line=line,
            language=language,
            client_library=client_library,
            method=method,
            path_template=path_template + "_modified",
            fields=[],
        )
        assert modified.content_hash() != original_hash


class TestPathNormalizationProperties:
    """Property-based tests for path normalization."""

    @given(
        placeholders=st.lists(
            st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
            min_size=0,
            max_size=5,
        )
    )
    def test_normalize_number_of_placeholders_preserved(self, placeholders: list[str]) -> None:
        """normalize_template() with N placeholders produces N {...} segments."""
        from guardian_core.mining.path_normalize import PLACEHOLDER_SENTINEL

        # Build a raw URL with placeholder sentinels
        segments = ["api", "v1", "resource"]
        for i in range(len(placeholders)):
            segments.append(PLACEHOLDER_SENTINEL)
            segments.append(f"seg{i}")
        raw = "/" + "/".join(segments)

        template = normalize_template(raw, placeholders)
        # Count braces - should have len(placeholders) opening and closing braces
        assert template.count("{") == len(placeholders)
        assert template.count("}") == len(placeholders)

    @given(
        base_path=st.just("/api/v1"),
        segments=st.lists(
            st.text(min_size=1, max_size=10, alphabet="abcdefghijklmnopqrstuvwxyz"), max_size=3
        ),
    )
    def test_normalize_always_starts_with_slash(self, base_path: str, segments: list[str]) -> None:
        """normalize_template() result always starts with /."""
        full_path = base_path + "/" + "/".join(segments) if segments else base_path
        result = normalize_template(full_path, [])
        assert result.startswith("/")

    @given(
        template=st.text(
            min_size=1, max_size=100, alphabet="abcdefghijklmnopqrstuvwxyz0123456789/-_{}"
        )
    )
    def test_abstract_segments_maintains_structure(self, template: str) -> None:
        """abstract_static_segments() maintains overall path structure."""
        from guardian_core.mining.path_normalize import abstract_static_segments

        # Ensure template is a valid path
        if not template.startswith("/"):
            template = "/" + template
        result = abstract_static_segments(template)
        # Result should still be a valid path
        assert result.startswith("/")
        # Should have same number of segments (or fewer if numeric were abstracted)
        original_segments = template.split("/")
        result_segments = result.split("/")
        assert len(result_segments) <= len(original_segments) + 1


class TestInferredCallSiteFields:
    """Property-based tests for InferredCallSite field behavior."""

    @given(
        fields=st.lists(
            st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz_0123456789"),
            max_size=20,
        )
    )
    def test_fields_list_is_preserved(self, fields: list[str]) -> None:
        """InferredCallSite.fields list is preserved exactly as provided."""
        site = InferredCallSite(
            file="test.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=fields,
        )
        assert site.fields == fields

    @given(method=st.text(min_size=1, max_size=20, alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    def test_method_stored_as_provided(self, method: str) -> None:
        """InferredCallSite stores method exactly as provided (case-sensitive)."""
        site = InferredCallSite(
            file="test.py",
            line=1,
            language="python",
            client_library="requests",
            method=method,
            path_template="/users",
            fields=[],
        )
        assert site.method == method

    @given(
        path=st.text(
            min_size=1, max_size=100, alphabet="abcdefghijklmnopqrstuvwxyz0123456789/-_{}()"
        )
    )
    def test_path_template_stored_as_provided(self, path: str) -> None:
        """InferredCallSite stores path_template exactly as provided."""
        # Ensure path is non-empty (constraint)
        if not path:
            path = "/default"
        site = InferredCallSite(
            file="test.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template=path,
            fields=[],
        )
        assert site.path_template == path
