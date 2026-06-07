"""Property-based tests for Spectral integration.

Invariants tested:
1. run_spectral returns empty list when spectral binary not available
2. run_spectral returns empty list for empty specs
3. run_spectral always returns a list of SpectralFindings
4. Spectral findings have valid code and message fields
5. Integration with diff_contracts spectral flag
"""

from __future__ import annotations

from typing import Any

from guardian_diff import diff_contracts
from guardian_diff.models import SpectralFinding
from guardian_diff.spectral import run_spectral
from hypothesis import given
from hypothesis import strategies as st


def _valid_openapi_spec() -> st.SearchStrategy[dict[str, Any]]:
    """Generate valid OpenAPI 3.x specification."""
    return st.just(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/test": {
                    "get": {
                        "summary": "Test endpoint",
                        "responses": {"200": {"description": "Success"}},
                    }
                }
            },
        }
    )


class TestSpectralIntegration:
    """Property tests for Spectral integration."""

    @given(_valid_openapi_spec())
    def test_run_spectral_returns_list(self, spec: dict[str, Any]) -> None:
        """run_spectral always returns a list."""
        result = run_spectral(spec)
        assert isinstance(result, list)

    @given(_valid_openapi_spec())
    def test_run_spectral_items_are_findings(self, spec: dict[str, Any]) -> None:
        """All items returned by run_spectral are SpectralFinding objects."""
        result = run_spectral(spec)
        for item in result:
            assert isinstance(item, SpectralFinding)

    def test_run_spectral_empty_spec_returns_empty_list(self) -> None:
        """run_spectral returns empty list for empty spec."""
        result = run_spectral({})
        assert result == []

    def test_run_spectral_none_spec_returns_empty_list(self) -> None:
        """run_spectral returns empty list for None spec."""
        # The function handles this gracefully
        result = run_spectral(None)  # type: ignore
        assert result == []

    @given(_valid_openapi_spec())
    def test_run_spectral_findings_have_code(self, spec: dict[str, Any]) -> None:
        """All Spectral findings have a non-empty code."""
        result = run_spectral(spec)
        for finding in result:
            assert isinstance(finding.code, str)
            assert len(finding.code) > 0

    @given(_valid_openapi_spec())
    def test_run_spectral_findings_have_message(self, spec: dict[str, Any]) -> None:
        """All Spectral findings have a non-empty message."""
        result = run_spectral(spec)
        for finding in result:
            assert isinstance(finding.message, str)
            assert len(finding.message) > 0

    @given(_valid_openapi_spec())
    def test_run_spectral_findings_have_severity(self, spec: dict[str, Any]) -> None:
        """All Spectral findings have a severity."""
        result = run_spectral(spec)
        for finding in result:
            assert isinstance(finding.severity, int)

    @given(_valid_openapi_spec())
    def test_run_spectral_findings_have_path(self, spec: dict[str, Any]) -> None:
        """All Spectral findings have a path (list)."""
        result = run_spectral(spec)
        for finding in result:
            assert isinstance(finding.path, list)


class TestSpectralWithDiffContracts:
    """Property tests for Spectral integration with diff_contracts."""

    @given(_valid_openapi_spec(), _valid_openapi_spec())
    def test_diff_contracts_spectral_flag_true(
        self, spec1: dict[str, Any], spec2: dict[str, Any]
    ) -> None:
        """diff_contracts with spectral=True includes spectral_findings."""
        report = diff_contracts(
            kind="openapi",
            before=spec1,
            after=spec2,
            spectral=True,
        )
        assert isinstance(report.spectral_findings, list)
        # All items should be SpectralFindings
        for finding in report.spectral_findings:
            assert isinstance(finding, SpectralFinding)

    @given(_valid_openapi_spec(), _valid_openapi_spec())
    def test_diff_contracts_spectral_flag_false(
        self, spec1: dict[str, Any], spec2: dict[str, Any]
    ) -> None:
        """diff_contracts with spectral=False has empty spectral_findings."""
        report = diff_contracts(
            kind="openapi",
            before=spec1,
            after=spec2,
            spectral=False,
        )
        assert report.spectral_findings == []

    @given(_valid_openapi_spec(), _valid_openapi_spec())
    def test_diff_contracts_spectral_findings_dont_affect_changes(
        self, spec1: dict[str, Any], spec2: dict[str, Any]
    ) -> None:
        """Spectral findings don't affect the changes list."""
        report_with = diff_contracts(
            kind="openapi",
            before=spec1,
            after=spec2,
            spectral=True,
        )
        report_without = diff_contracts(
            kind="openapi",
            before=spec1,
            after=spec2,
            spectral=False,
        )
        # The changes should be identical regardless of spectral flag
        assert len(report_with.changes) == len(report_without.changes)

    def test_diff_contracts_proto_ignores_spectral_flag(self) -> None:
        """diff_contracts with proto kind ignores spectral flag."""
        report = diff_contracts(
            kind="proto",
            before=b"",
            after=b"",
            spectral=True,  # Should be ignored
        )
        assert report.spectral_findings == []


class TestSpectralFindingValidation:
    """Property tests for SpectralFinding model validation."""

    def test_spectral_finding_minimal_valid(self) -> None:
        """SpectralFinding can be constructed with minimal fields."""
        finding = SpectralFinding(
            code="RULE",
            message="A message",
            severity=1,
        )
        assert finding.code == "RULE"
        assert finding.message == "A message"
        assert finding.severity == 1
        assert finding.path == []

    def test_spectral_finding_with_path(self) -> None:
        """SpectralFinding can include a path."""
        finding = SpectralFinding(
            code="RULE",
            message="A message",
            severity=2,
            path=["paths", "users", "get", "responses", "200"],
        )
        assert finding.path == ["paths", "users", "get", "responses", "200"]

    @given(st.text(min_size=1, max_size=100), st.integers(min_value=0, max_value=10))
    def test_spectral_finding_various_codes_and_severities(self, code: str, severity: int) -> None:
        """SpectralFinding accepts various code and severity values."""
        finding = SpectralFinding(
            code=code,
            message="Test message",
            severity=severity,
        )
        assert finding.code == code
        assert finding.severity == severity
