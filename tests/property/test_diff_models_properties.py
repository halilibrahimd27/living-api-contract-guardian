"""Property-based tests for diff model invariants and validations.

Invariants tested:
1. RawChange model constraints (kind/location non-empty, max lengths)
2. ChangeRecord model constraints (change_id format, verdict validity)
3. ChangeReportSummary consistency (verdict counts sum to total)
4. SpectralFinding model constraints (code/message non-empty)
5. ChangeReport consistency (summary matches actual changes)
6. Model serialization round-trips
"""

from __future__ import annotations

import pytest
from guardian_diff.engine import _change_id, _summarize, classify_changes
from guardian_diff.models import (
    ChangeRecord,
    ChangeReport,
    ChangeReportSummary,
    RawChange,
    SpectralFinding,
    Verdict,
)
from guardian_diff.ruleset import load_default_rules
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

# Strategies for model construction

def _raw_change_strategy() -> st.SearchStrategy[RawChange]:
    """Generate valid RawChange objects."""
    kinds = st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz_.",
        min_size=1,
        max_size=128,
    )
    locations = st.text(
        alphabet="/abcdefghijklmnopqrstuvwxyz0123456789_-.",
        min_size=1,
        max_size=2048,
    )
    values = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.text(max_size=500),
    )
    details = st.dictionaries(
        keys=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=20),
        values=st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=100)),
        max_size=5,
    )

    return st.builds(
        RawChange,
        kind=kinds,
        location=locations,
        before=values,
        after=values,
        detail=details,
    )


def _verdict_strategy() -> st.SearchStrategy[Verdict]:
    """Generate valid Verdict values."""
    return st.sampled_from(["additive", "behavioral", "breaking"])


def _change_record_strategy() -> st.SearchStrategy[ChangeRecord]:
    """Generate valid ChangeRecord objects."""
    return st.builds(
        ChangeRecord,
        change_id=st.text(
            alphabet="0123456789abcdef",
            min_size=16,
            max_size=16,
        ),
        kind=st.text(min_size=1, max_size=128),
        location=st.text(min_size=1, max_size=2048),
        verdict=_verdict_strategy(),
        rule_id=st.text(min_size=1, max_size=64),
        rationale=st.text(min_size=1, max_size=2048),
        affected_clients=st.lists(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1, max_size=50),
            max_size=10,
        ),
    )


def _spectral_finding_strategy() -> st.SearchStrategy[SpectralFinding]:
    """Generate valid SpectralFinding objects."""
    return st.builds(
        SpectralFinding,
        code=st.text(
            alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            min_size=1,
            max_size=100,
        ),
        message=st.text(min_size=1, max_size=1000),
        severity=st.integers(min_value=0, max_value=5),
        path=st.lists(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789/_", min_size=1, max_size=50),
            max_size=10,
        ),
    )


class TestRawChangeModel:
    """Property tests for RawChange model constraints."""

    @given(_raw_change_strategy())
    def test_raw_change_kind_is_non_empty(self, change: RawChange) -> None:
        """RawChange.kind must be non-empty."""
        assert isinstance(change.kind, str)
        assert len(change.kind) > 0
        assert len(change.kind) <= 128

    @given(_raw_change_strategy())
    def test_raw_change_location_is_non_empty(self, change: RawChange) -> None:
        """RawChange.location must be non-empty."""
        assert isinstance(change.location, str)
        assert len(change.location) > 0
        assert len(change.location) <= 2048

    @given(_raw_change_strategy())
    def test_raw_change_before_after_optional(self, change: RawChange) -> None:
        """RawChange.before and after can be None or any object."""
        # Just verify they're present and can be None
        assert hasattr(change, "before")
        assert hasattr(change, "after")

    @given(_raw_change_strategy())
    def test_raw_change_detail_is_dict(self, change: RawChange) -> None:
        """RawChange.detail is always a dictionary."""
        assert isinstance(change.detail, dict)

    @given(_raw_change_strategy())
    def test_raw_change_is_equal_to_itself(self, change: RawChange) -> None:
        """RawChange supports value equality (Pydantic v2 default)."""
        # Pydantic v2 models compare by field values, not by identity.
        same = RawChange(
            kind=change.kind,
            location=change.location,
            before=change.before,
            after=change.after,
            detail=change.detail,
        )
        assert change == same

    def test_raw_change_rejects_invalid_kind(self) -> None:
        """RawChange rejects kind that exceeds max length."""
        with pytest.raises(ValidationError):
            RawChange(
                kind="x" * 129,  # Exceeds max_length=128
                location="/test",
            )

    def test_raw_change_rejects_invalid_location(self) -> None:
        """RawChange rejects location that exceeds max length."""
        with pytest.raises(ValidationError):
            RawChange(
                kind="test.kind",
                location="/" + "x" * 2048,  # Exceeds max_length=2048
            )

    def test_raw_change_rejects_empty_kind(self) -> None:
        """RawChange rejects empty kind."""
        with pytest.raises(ValidationError):
            RawChange(kind="", location="/test")

    def test_raw_change_rejects_empty_location(self) -> None:
        """RawChange rejects empty location."""
        with pytest.raises(ValidationError):
            RawChange(kind="test.kind", location="")


class TestChangeRecordModel:
    """Property tests for ChangeRecord model constraints."""

    @given(_change_record_strategy())
    def test_change_record_change_id_format(self, record: ChangeRecord) -> None:
        """ChangeRecord.change_id is always 16 hex characters."""
        assert isinstance(record.change_id, str)
        assert len(record.change_id) == 16
        assert all(c in "0123456789abcdef" for c in record.change_id)

    @given(_change_record_strategy())
    def test_change_record_verdict_is_valid(self, record: ChangeRecord) -> None:
        """ChangeRecord.verdict is always in {additive, behavioral, breaking}."""
        assert record.verdict in {"additive", "behavioral", "breaking"}

    @given(_change_record_strategy())
    def test_change_record_rule_id_non_empty(self, record: ChangeRecord) -> None:
        """ChangeRecord.rule_id is non-empty."""
        assert isinstance(record.rule_id, str)
        assert len(record.rule_id) > 0

    @given(_change_record_strategy())
    def test_change_record_rationale_non_empty(self, record: ChangeRecord) -> None:
        """ChangeRecord.rationale is non-empty."""
        assert isinstance(record.rationale, str)
        assert len(record.rationale) > 0

    @given(_change_record_strategy())
    def test_change_record_affected_clients_is_list(self, record: ChangeRecord) -> None:
        """ChangeRecord.affected_clients is always a list."""
        assert isinstance(record.affected_clients, list)
        assert all(isinstance(c, str) for c in record.affected_clients)

    @given(_change_record_strategy())
    def test_change_record_detail_is_dict(self, record: ChangeRecord) -> None:
        """ChangeRecord.detail is always a dictionary."""
        assert isinstance(record.detail, dict)

    @given(_raw_change_strategy())
    def test_change_record_from_raw_change(self, raw: RawChange) -> None:
        """ChangeRecord can be constructed from RawChange data."""
        record = ChangeRecord(
            change_id=_change_id(raw),
            kind=raw.kind,
            location=raw.location,
            verdict="behavioral",
            rule_id="TEST",
            rationale="test",
            before=raw.before,
            after=raw.after,
            detail=raw.detail,
        )
        assert record.kind == raw.kind
        assert record.location == raw.location
        assert record.before == raw.before
        assert record.after == raw.after


class TestChangeReportSummaryModel:
    """Property tests for ChangeReportSummary model consistency."""

    def test_summary_default_is_all_zeros(self) -> None:
        """Default ChangeReportSummary has all counts as 0."""
        summary = ChangeReportSummary()
        assert summary.total == 0
        assert summary.additive == 0
        assert summary.behavioral == 0
        assert summary.breaking == 0

    @given(
        st.integers(min_value=0, max_value=100),
        st.integers(min_value=0, max_value=100),
        st.integers(min_value=0, max_value=100),
    )
    def test_summary_subtotals_sum_to_total(
        self, add: int, behav: int, brk: int
    ) -> None:
        """ChangeReportSummary subtotals sum to total."""
        summary = ChangeReportSummary(
            total=add + behav + brk,
            additive=add,
            behavioral=behav,
            breaking=brk,
        )
        assert summary.total == add + behav + brk

    @given(st.lists(_change_record_strategy(), max_size=100))
    def test_summarize_counts_verdicts_correctly(
        self, records: list[ChangeRecord]
    ) -> None:
        """_summarize counts verdicts correctly."""
        summary = _summarize(records)
        assert summary.total == len(records)

        additive = sum(1 for r in records if r.verdict == "additive")
        behavioral = sum(1 for r in records if r.verdict == "behavioral")
        breaking = sum(1 for r in records if r.verdict == "breaking")

        assert summary.additive == additive
        assert summary.behavioral == behavioral
        assert summary.breaking == breaking

    @given(st.lists(_change_record_strategy(), max_size=100))
    def test_summarize_subtotals_sum_to_total(
        self, records: list[ChangeRecord]
    ) -> None:
        """_summarize subtotals always sum to total."""
        summary = _summarize(records)
        assert (
            summary.additive + summary.behavioral + summary.breaking == summary.total
        )


class TestSpectralFindingModel:
    """Property tests for SpectralFinding model constraints."""

    @given(_spectral_finding_strategy())
    def test_spectral_finding_code_non_empty(self, finding: SpectralFinding) -> None:
        """SpectralFinding.code is non-empty."""
        assert isinstance(finding.code, str)
        assert len(finding.code) > 0

    @given(_spectral_finding_strategy())
    def test_spectral_finding_message_non_empty(self, finding: SpectralFinding) -> None:
        """SpectralFinding.message is non-empty."""
        assert isinstance(finding.message, str)
        assert len(finding.message) > 0

    @given(_spectral_finding_strategy())
    def test_spectral_finding_severity_is_int(self, finding: SpectralFinding) -> None:
        """SpectralFinding.severity is an integer."""
        assert isinstance(finding.severity, int)

    @given(_spectral_finding_strategy())
    def test_spectral_finding_path_is_list(self, finding: SpectralFinding) -> None:
        """SpectralFinding.path is always a list."""
        assert isinstance(finding.path, list)
        assert all(isinstance(p, str) for p in finding.path)

    def test_spectral_finding_empty_path_is_valid(self) -> None:
        """SpectralFinding can have an empty path."""
        finding = SpectralFinding(
            code="TEST",
            message="Test message",
            severity=1,
            path=[],
        )
        assert finding.path == []


class TestChangeReportModel:
    """Property tests for ChangeReport model consistency."""

    def test_change_report_default_is_valid(self) -> None:
        """Default ChangeReport is valid."""
        report = ChangeReport(contract_kind="openapi")
        assert report.contract_kind == "openapi"
        assert report.changes == []
        assert report.summary.total == 0
        assert report.spectral_findings == []
        assert report.ruleset_id == "default"

    @given(
        st.sampled_from(["openapi", "proto"]),
        st.lists(_change_record_strategy(), max_size=50),
        st.lists(_spectral_finding_strategy(), max_size=20),
    )
    def test_change_report_summary_matches_changes(
        self,
        kind: str,
        changes: list[ChangeRecord],
        findings: list[SpectralFinding],
    ) -> None:
        """ChangeReport.summary counts match actual changes when summary is computed."""
        expected_summary = _summarize(changes)
        report = ChangeReport(
            contract_kind=kind,
            changes=changes,
            summary=expected_summary,
            spectral_findings=findings,
        )
        assert report.summary.total == expected_summary.total
        assert report.summary.additive == expected_summary.additive
        assert report.summary.behavioral == expected_summary.behavioral
        assert report.summary.breaking == expected_summary.breaking

    @given(
        st.lists(_raw_change_strategy(), max_size=30),
    )
    def test_change_report_all_changes_have_valid_verdict(
        self, raw_changes: list[RawChange]
    ) -> None:
        """All changes in ChangeReport have valid verdict values."""
        ruleset = load_default_rules()
        records = classify_changes(raw_changes, ruleset=ruleset)
        report = ChangeReport(contract_kind="openapi", changes=records)

        for change in report.changes:
            assert change.verdict in {"additive", "behavioral", "breaking"}


class TestModelSerialization:
    """Property tests for model serialization round-trips."""

    @given(_raw_change_strategy())
    def test_raw_change_json_serialization(self, change: RawChange) -> None:
        """RawChange can be serialized to JSON and deserialized."""
        json_str = change.model_dump_json()
        restored = RawChange.model_validate_json(json_str)
        assert restored.kind == change.kind
        assert restored.location == change.location
        assert restored.before == change.before
        assert restored.after == change.after

    @given(_change_record_strategy())
    def test_change_record_json_serialization(self, record: ChangeRecord) -> None:
        """ChangeRecord can be serialized to JSON and deserialized."""
        json_str = record.model_dump_json()
        restored = ChangeRecord.model_validate_json(json_str)
        assert restored.change_id == record.change_id
        assert restored.verdict == record.verdict
        assert restored.rule_id == record.rule_id

    @given(_spectral_finding_strategy())
    def test_spectral_finding_json_serialization(
        self, finding: SpectralFinding
    ) -> None:
        """SpectralFinding can be serialized to JSON and deserialized."""
        json_str = finding.model_dump_json()
        restored = SpectralFinding.model_validate_json(json_str)
        assert restored.code == finding.code
        assert restored.message == finding.message
        assert restored.severity == finding.severity

    def test_change_report_json_serialization(self) -> None:
        """ChangeReport can be serialized to JSON and deserialized."""
        report = ChangeReport(
            contract_kind="openapi",
            changes=[
                ChangeRecord(
                    change_id="0" * 16,
                    kind="test.kind",
                    location="/test",
                    verdict="additive",
                    rule_id="TEST",
                    rationale="test",
                )
            ],
        )
        json_str = report.model_dump_json()
        restored = ChangeReport.model_validate_json(json_str)
        assert restored.contract_kind == "openapi"
        assert len(restored.changes) == 1
        assert restored.changes[0].verdict == "additive"
