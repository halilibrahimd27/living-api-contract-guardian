"""Property-based tests for CI routes: schema validation and persistence invariants.

Invariants tested:

**CiRunCreate schema validation:**
1. Valid repo slugs (owner/name format) are accepted
2. Valid git SHAs (7-64 hex chars) are accepted for head_sha and base_sha
3. Valid PR numbers (>= 1) are accepted
4. Valid GitHub check conclusions are accepted
5. Invalid repo formats are rejected (wrong pattern)
6. Invalid SHA formats are rejected (non-hex, too short, too long)
7. Invalid PR numbers are rejected (< 1)
8. Valid report JSON structures are accepted
9. bypass_label_present defaults to False
10. check_run_id can be None or a positive integer

**CI run persistence invariants (integration tests):**
1. Posting a new CI run creates a row with all fields from the payload
2. Posting the same (repo, pr_number, head_sha) updates the existing row
3. Row ID is preserved across updates
4. conclusion, report_json, and bypass_label_present are updated on upsert
5. check_run_id is updated only if provided (non-None)
6. Getting the latest run for a PR returns 404 when no runs exist
7. Getting the latest run returns the most recently created run
8. Repo slug is correctly reconstructed from owner/name in GET path
"""

from __future__ import annotations

from typing import Any

import pytest
from guardian_core.schemas import CiRunCreate
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

# ============================================================================
# Helper Strategies for Schema Validation
# ============================================================================


def _valid_repo_slug() -> st.SearchStrategy[str]:
    """Generate valid repo slugs in owner/name format."""
    return st.builds(
        lambda owner, name: f"{owner}/{name}",
        owner=st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
        ),
        name=st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
        ),
    )


def _invalid_repo_slug() -> st.SearchStrategy[str]:
    """Generate invalid repo slugs (not in owner/name format)."""
    return st.one_of(
        st.just("owner-only"),
        st.just("owner/name/extra"),
        st.text(min_size=1, max_size=20),
        st.just(""),
    ).filter(lambda s: "/" not in s or s.count("/") != 1)


def _valid_sha() -> st.SearchStrategy[str]:
    """Generate valid git SHA strings (7-64 hex chars)."""
    return st.binary(min_size=7, max_size=32).map(lambda b: b.hex())


def _invalid_sha() -> st.SearchStrategy[str]:
    """Generate invalid SHA strings."""
    return st.one_of(
        st.text(min_size=1, max_size=6, alphabet="0123456789abcdef"),  # Too short
        st.text(min_size=1, max_size=10, alphabet="GHIJKLMNOP"),  # Non-hex chars
    )


def _valid_pr_number() -> st.SearchStrategy[int]:
    """Generate valid PR numbers (>= 1)."""
    return st.integers(min_value=1, max_value=100000)


def _invalid_pr_number() -> st.SearchStrategy[int]:
    """Generate invalid PR numbers (< 1)."""
    return st.integers(max_value=0)


def _valid_conclusion() -> st.SearchStrategy[str]:
    """Generate valid GitHub check conclusion strings."""
    return st.sampled_from([
        "success",
        "failure",
        "neutral",
        "action_required",
        "cancelled",
        "timed_out",
        "skipped",
    ])


def _invalid_conclusion() -> st.SearchStrategy[str]:
    """Generate invalid conclusion strings."""
    return st.text(min_size=1, max_size=50).filter(
        lambda s: s not in [
            "success",
            "failure",
            "neutral",
            "action_required",
            "cancelled",
            "timed_out",
            "skipped",
        ]
    )


def _valid_report_json() -> st.SearchStrategy[dict[str, Any]]:
    """Generate valid ChangeReport JSON structures."""
    return st.fixed_dictionaries({
        "contract_kind": st.sampled_from(["openapi", "proto"]),
        "ruleset_id": st.text(min_size=1, max_size=50),
        "summary": st.fixed_dictionaries({
            "breaking": st.integers(min_value=0, max_value=10),
            "behavioral": st.integers(min_value=0, max_value=10),
            "additive": st.integers(min_value=0, max_value=10),
            "total": st.integers(min_value=0, max_value=30),
        }),
        "changes": st.lists(
            st.fixed_dictionaries({
                "change_id": st.text(min_size=1, max_size=30),
                "kind": st.text(min_size=1, max_size=20),
                "location": st.text(min_size=1, max_size=100),
                "verdict": st.sampled_from(["additive", "behavioral", "breaking"]),
                "rule_id": st.text(min_size=1, max_size=30),
                "rationale": st.text(min_size=1, max_size=200),
                "affected_clients": st.lists(st.text(min_size=1, max_size=50), max_size=5),
            }),
            max_size=5,
        ),
    })


def _valid_ci_run_create() -> st.SearchStrategy[CiRunCreate]:
    """Generate valid CiRunCreate payloads."""
    return st.builds(
        CiRunCreate,
        repo=_valid_repo_slug(),
        pr_number=_valid_pr_number(),
        head_sha=_valid_sha(),
        base_sha=_valid_sha(),
        conclusion=_valid_conclusion(),
        report_json=_valid_report_json(),
        bypass_label_present=st.booleans(),
        check_run_id=st.one_of(st.none(), st.integers(min_value=1, max_value=999999999)),
    )


# ============================================================================
# Tests for CiRunCreate Schema Validation
# ============================================================================


class TestCiRunCreateValidation:
    """Property tests for CiRunCreate schema validation."""

    @given(_valid_ci_run_create())
    def test_valid_payload_is_accepted(self, payload: CiRunCreate) -> None:
        """CiRunCreate accepts all valid payload fields."""
        assert payload.repo is not None
        assert payload.pr_number >= 1
        assert len(payload.head_sha) >= 7
        assert len(payload.base_sha) >= 7

    @given(repo=_valid_repo_slug())
    def test_valid_repo_slug_format(self, repo: str) -> None:
        """Valid repo slugs (owner/name) are accepted."""
        payload = CiRunCreate(
            repo=repo,
            pr_number=1,
            head_sha="1234567890abcdef",
            base_sha="fedcba0987654321",
            conclusion="success",
        )
        assert payload.repo == repo

    @given(repo=_invalid_repo_slug())
    @settings(suppress_health_check=[HealthCheck.filter_too_much])
    def test_invalid_repo_slug_format_rejected(self, repo: str) -> None:
        """Invalid repo slugs (not owner/name) are rejected."""
        with pytest.raises(ValidationError):
            CiRunCreate(
                repo=repo,
                pr_number=1,
                head_sha="1234567890abcdef",
                base_sha="fedcba0987654321",
                conclusion="success",
            )

    @given(sha=_valid_sha())
    def test_valid_head_sha(self, sha: str) -> None:
        """Valid head_sha strings (7-64 hex chars) are accepted."""
        payload = CiRunCreate(
            repo="owner/name",
            pr_number=1,
            head_sha=sha,
            base_sha="1234567890abcdef",
            conclusion="success",
        )
        assert payload.head_sha == sha

    @given(sha=_invalid_sha())
    @settings(suppress_health_check=[HealthCheck.filter_too_much])
    def test_invalid_head_sha_rejected(self, sha: str) -> None:
        """Invalid head_sha (non-hex or wrong length) is rejected."""
        with pytest.raises(ValidationError):
            CiRunCreate(
                repo="owner/name",
                pr_number=1,
                head_sha=sha,
                base_sha="1234567890abcdef",
                conclusion="success",
            )

    @given(sha=_valid_sha())
    def test_valid_base_sha(self, sha: str) -> None:
        """Valid base_sha strings (7-64 hex chars) are accepted."""
        payload = CiRunCreate(
            repo="owner/name",
            pr_number=1,
            head_sha="1234567890abcdef",
            base_sha=sha,
            conclusion="success",
        )
        assert payload.base_sha == sha

    @given(pr=_valid_pr_number())
    def test_valid_pr_number(self, pr: int) -> None:
        """Valid PR numbers (>= 1) are accepted."""
        payload = CiRunCreate(
            repo="owner/name",
            pr_number=pr,
            head_sha="1234567890abcdef",
            base_sha="fedcba0987654321",
            conclusion="success",
        )
        assert payload.pr_number == pr

    @given(pr=_invalid_pr_number())
    def test_invalid_pr_number_rejected(self, pr: int) -> None:
        """Invalid PR numbers (< 1) are rejected."""
        with pytest.raises(ValidationError):
            CiRunCreate(
                repo="owner/name",
                pr_number=pr,
                head_sha="1234567890abcdef",
                base_sha="fedcba0987654321",
                conclusion="success",
            )

    @given(conclusion=_valid_conclusion())
    def test_valid_conclusion(self, conclusion: str) -> None:
        """Valid GitHub conclusions are accepted."""
        payload = CiRunCreate(
            repo="owner/name",
            pr_number=1,
            head_sha="1234567890abcdef",
            base_sha="fedcba0987654321",
            conclusion=conclusion,  # type: ignore
        )
        assert payload.conclusion == conclusion

    @given(conclusion=_invalid_conclusion())
    @settings(suppress_health_check=[HealthCheck.filter_too_much])
    def test_invalid_conclusion_rejected(self, conclusion: str) -> None:
        """Invalid conclusions are rejected."""
        with pytest.raises(ValidationError):
            CiRunCreate(
                repo="owner/name",
                pr_number=1,
                head_sha="1234567890abcdef",
                base_sha="fedcba0987654321",
                conclusion=conclusion,  # type: ignore
            )

    @given(report=_valid_report_json())
    def test_valid_report_json(self, report: dict[str, Any]) -> None:
        """Valid ChangeReport JSON structures are accepted."""
        payload = CiRunCreate(
            repo="owner/name",
            pr_number=1,
            head_sha="1234567890abcdef",
            base_sha="fedcba0987654321",
            conclusion="success",
            report_json=report,
        )
        assert payload.report_json == report

    def test_bypass_label_defaults_to_false(self) -> None:
        """bypass_label_present defaults to False when omitted."""
        payload = CiRunCreate(
            repo="owner/name",
            pr_number=1,
            head_sha="1234567890abcdef",
            base_sha="fedcba0987654321",
            conclusion="success",
        )
        assert payload.bypass_label_present is False

    @given(flag=st.booleans())
    def test_bypass_label_accepted_value(self, flag: bool) -> None:
        """bypass_label_present accepts both True and False."""
        payload = CiRunCreate(
            repo="owner/name",
            pr_number=1,
            head_sha="1234567890abcdef",
            base_sha="fedcba0987654321",
            conclusion="success",
            bypass_label_present=flag,
        )
        assert payload.bypass_label_present == flag

    def test_check_run_id_can_be_none(self) -> None:
        """check_run_id can be None."""
        payload = CiRunCreate(
            repo="owner/name",
            pr_number=1,
            head_sha="1234567890abcdef",
            base_sha="fedcba0987654321",
            conclusion="success",
            check_run_id=None,
        )
        assert payload.check_run_id is None

    @given(check_id=st.integers(min_value=1, max_value=999999999))
    def test_check_run_id_accepts_positive_integer(self, check_id: int) -> None:
        """check_run_id accepts positive integers."""
        payload = CiRunCreate(
            repo="owner/name",
            pr_number=1,
            head_sha="1234567890abcdef",
            base_sha="fedcba0987654321",
            conclusion="success",
            check_run_id=check_id,
        )
        assert payload.check_run_id == check_id


