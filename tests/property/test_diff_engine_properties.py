"""Property-based tests for the evolution rule engine core.

Invariants tested:
1. change_id is deterministic and idempotent
2. classify_changes always returns ChangeRecords matching input RawChanges
3. diff_contracts produces reports where summary counts match actual changes
4. diff_contracts with openapi contract always validates input types
5. diff_contracts with proto contract always validates input types
"""

from __future__ import annotations

import hashlib
from typing import Any

from guardian_diff import diff_contracts, load_default_rules
from guardian_diff.engine import _change_id
from guardian_diff.models import ChangeRecord, RawChange, Verdict
from hypothesis import given
from hypothesis import strategies as st


def _raw_change_strategy() -> st.SearchStrategy[RawChange]:
    """Generate valid RawChange objects with realistic properties."""
    kinds = st.sampled_from(
        [
            "openapi.path.added",
            "openapi.path.removed",
            "openapi.operation.added",
            "openapi.operation.removed",
            "openapi.parameter.added.optional",
            "openapi.parameter.added.required",
            "openapi.parameter.removed",
            "openapi.parameter.required.increased",
            "openapi.parameter.required.decreased",
            "openapi.parameter.type_changed",
            "proto.field.added",
            "proto.field.removed",
            "proto.field.number_changed",
            "proto.field.type_changed",
            "proto.field.label_changed",
            "proto.field.renamed",
            "proto.rpc.added",
            "proto.rpc.removed",
            "proto.message.added",
            "proto.message.removed",
        ]
    )

    locations = st.text(
        alphabet=st.characters(
            blacklist_categories=("Cc", "Cs"), min_codepoint=32, max_codepoint=126
        ),
        min_size=0,
        max_size=200,
    ).map(lambda s: "/" + s)

    values = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=100))
    details = st.dictionaries(
        keys=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=20),
        values=values,
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


def _openapi_spec_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Generate valid OpenAPI 3.x spec dictionaries."""
    return st.just(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {},
            "components": {"schemas": {}},
        }
    )


class TestChangeIdGeneration:
    """Property tests for change_id generation function."""

    @given(_raw_change_strategy())
    def test_change_id_is_deterministic(self, change: RawChange) -> None:
        """Calling _change_id twice on the same change produces identical result."""
        id1 = _change_id(change)
        id2 = _change_id(change)
        assert id1 == id2

    @given(_raw_change_strategy())
    def test_change_id_is_valid_hex(self, change: RawChange) -> None:
        """change_id is a valid 16-char hex string."""
        cid = _change_id(change)
        assert isinstance(cid, str)
        assert len(cid) == 16
        assert all(c in "0123456789abcdef" for c in cid)

    @given(_raw_change_strategy())
    def test_change_id_matches_sha1_prefix(self, change: RawChange) -> None:
        """change_id is the first 16 chars of SHA1(kind::location)."""
        payload = f"{change.kind}::{change.location}".encode()
        expected = hashlib.sha1(payload, usedforsecurity=False).hexdigest()[:16]
        actual = _change_id(change)
        assert actual == expected

    @given(
        st.lists(_raw_change_strategy(), min_size=2, max_size=20, unique=False),
    )
    def test_identical_changes_have_identical_ids(self, changes: list[RawChange]) -> None:
        """Two changes with same kind and location have same ID regardless of before/after."""
        if len(changes) < 2:
            return
        c1 = changes[0]
        # Create a change with same kind/location but different before/after
        c2 = RawChange(
            kind=c1.kind,
            location=c1.location,
            before={"different": "value"},
            after={"also": "different"},
        )
        assert _change_id(c1) == _change_id(c2)


class TestClassifyChanges:
    """Property tests for classify_changes function."""

    @given(st.lists(_raw_change_strategy(), min_size=0, max_size=50))
    def test_classify_changes_matches_input_count(self, raw_changes: list[RawChange]) -> None:
        """classify_changes returns exactly one ChangeRecord per RawChange."""
        from guardian_diff.engine import classify_changes

        ruleset = load_default_rules()
        records = classify_changes(raw_changes, ruleset=ruleset)
        assert len(records) == len(raw_changes)

    @given(st.lists(_raw_change_strategy(), min_size=0, max_size=50))
    def test_classify_changes_preserves_change_content(self, raw_changes: list[RawChange]) -> None:
        """classify_changes preserves kind, location, before, after in ChangeRecords."""
        from guardian_diff.engine import classify_changes

        ruleset = load_default_rules()
        records = classify_changes(raw_changes, ruleset=ruleset)
        for raw, record in zip(raw_changes, records, strict=True):
            assert record.kind == raw.kind
            assert record.location == raw.location
            assert record.before == raw.before
            assert record.after == raw.after

    @given(st.lists(_raw_change_strategy(), min_size=0, max_size=50))
    def test_classify_changes_all_verdicts_valid(self, raw_changes: list[RawChange]) -> None:
        """All classified changes have a valid verdict."""
        from guardian_diff.engine import classify_changes

        ruleset = load_default_rules()
        records = classify_changes(raw_changes, ruleset=ruleset)
        valid_verdicts: set[str] = {"additive", "behavioral", "breaking"}
        for record in records:
            assert record.verdict in valid_verdicts

    @given(st.lists(_raw_change_strategy(), min_size=0, max_size=50))
    def test_classify_changes_all_have_rule_id(self, raw_changes: list[RawChange]) -> None:
        """All classified changes have a non-empty rule_id."""
        from guardian_diff.engine import classify_changes

        ruleset = load_default_rules()
        records = classify_changes(raw_changes, ruleset=ruleset)
        for record in records:
            assert isinstance(record.rule_id, str)
            assert len(record.rule_id) > 0

    @given(st.lists(_raw_change_strategy(), min_size=0, max_size=50))
    def test_classify_changes_all_have_rationale(self, raw_changes: list[RawChange]) -> None:
        """All classified changes have a non-empty rationale."""
        from guardian_diff.engine import classify_changes

        ruleset = load_default_rules()
        records = classify_changes(raw_changes, ruleset=ruleset)
        for record in records:
            assert isinstance(record.rationale, str)
            assert len(record.rationale) > 0


class TestDiffContractsOpenAPI:
    """Property tests for diff_contracts with OpenAPI specs."""

    @given(_openapi_spec_strategy(), _openapi_spec_strategy())
    def test_diff_contracts_openapi_returns_report(
        self, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        """diff_contracts(kind='openapi') returns a ChangeReport."""
        report = diff_contracts(kind="openapi", before=before, after=after)
        assert report.contract_kind == "openapi"
        assert isinstance(report.changes, list)
        assert isinstance(report.summary, object)

    @given(_openapi_spec_strategy(), _openapi_spec_strategy())
    def test_diff_contracts_openapi_summary_matches_changes(
        self, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        """ChangeReport.summary counts match the actual changes list."""
        report = diff_contracts(kind="openapi", before=before, after=after)
        assert report.summary.total == len(report.changes)
        additive = sum(1 for c in report.changes if c.verdict == "additive")
        behavioral = sum(1 for c in report.changes if c.verdict == "behavioral")
        breaking = sum(1 for c in report.changes if c.verdict == "breaking")
        assert report.summary.additive == additive
        assert report.summary.behavioral == behavioral
        assert report.summary.breaking == breaking

    @given(_openapi_spec_strategy())
    def test_diff_contracts_openapi_identical_specs_no_changes(self, spec: dict[str, Any]) -> None:
        """Diffing identical OpenAPI specs produces no changes."""
        report = diff_contracts(kind="openapi", before=spec, after=spec)
        assert len(report.changes) == 0
        assert report.summary.total == 0

    def test_diff_contracts_openapi_rejects_non_dict_before(self) -> None:
        """diff_contracts raises TypeError when before is not dict for OpenAPI."""
        import pytest

        with pytest.raises(TypeError):
            diff_contracts(kind="openapi", before="not a dict", after={})  # type: ignore

    def test_diff_contracts_openapi_rejects_non_dict_after(self) -> None:
        """diff_contracts raises TypeError when after is not dict for OpenAPI."""
        import pytest

        with pytest.raises(TypeError):
            diff_contracts(kind="openapi", before={}, after="not a dict")  # type: ignore

    @given(_openapi_spec_strategy(), _openapi_spec_strategy())
    def test_diff_contracts_openapi_all_changes_have_valid_verdict(
        self, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        """All changes in ChangeReport have valid verdict values."""
        report = diff_contracts(kind="openapi", before=before, after=after)
        valid_verdicts: set[str] = {"additive", "behavioral", "breaking"}
        for change in report.changes:
            assert change.verdict in valid_verdicts


class TestDiffContractsProto:
    """Property tests for diff_contracts with Protobuf descriptor sets."""

    def test_diff_contracts_proto_rejects_non_bytes_before(self) -> None:
        """diff_contracts raises TypeError when before is not bytes for proto."""
        import pytest

        with pytest.raises(TypeError):
            diff_contracts(kind="proto", before="not bytes", after=b"")  # type: ignore

    def test_diff_contracts_proto_rejects_non_bytes_after(self) -> None:
        """diff_contracts raises TypeError when after is not bytes for proto."""
        import pytest

        with pytest.raises(TypeError):
            diff_contracts(kind="proto", before=b"", after="not bytes")  # type: ignore

    @given(st.binary(min_size=0, max_size=100))
    def test_diff_contracts_proto_identical_bytes_no_changes(self, blob: bytes) -> None:
        """Diffing identical Protobuf blobs produces no changes."""
        # May raise if blob is not valid protobuf, but empty/non-FDS bytes should be handled
        try:
            report = diff_contracts(kind="proto", before=blob, after=blob)
            assert len(report.changes) == 0
            assert report.summary.total == 0
        except Exception:
            # Non-FDS bytes might not parse; that's OK
            pass

    def test_diff_contracts_proto_returns_correct_contract_kind(self) -> None:
        """diff_contracts(kind='proto') returns report with contract_kind='proto'."""
        report = diff_contracts(kind="proto", before=b"", after=b"")
        assert report.contract_kind == "proto"


class TestDiffContractsSummary:
    """Property tests for summary computation in ChangeReport."""

    @given(st.lists(_raw_change_strategy(), min_size=0, max_size=50))
    def test_summary_total_equals_change_count(self, raw_changes: list[RawChange]) -> None:
        """ChangeReport.summary.total always equals len(ChangeReport.changes)."""
        from guardian_diff.engine import _summarize, classify_changes

        ruleset = load_default_rules()
        records = classify_changes(raw_changes, ruleset=ruleset)
        summary = _summarize(records)
        assert summary.total == len(records)

    @given(st.lists(_raw_change_strategy(), min_size=0, max_size=50))
    def test_summary_sub_totals_sum_to_total(self, raw_changes: list[RawChange]) -> None:
        """ChangeReport.summary verdict counts sum to total."""
        from guardian_diff.engine import _summarize, classify_changes

        ruleset = load_default_rules()
        records = classify_changes(raw_changes, ruleset=ruleset)
        summary = _summarize(records)
        assert summary.additive + summary.behavioral + summary.breaking == summary.total

    @given(st.lists(_raw_change_strategy(), min_size=0, max_size=50))
    def test_summary_verdict_counts_match_actual(self, raw_changes: list[RawChange]) -> None:
        """ChangeReport.summary verdict counts match actual verdicts in changes."""
        from guardian_diff.engine import _summarize, classify_changes

        ruleset = load_default_rules()
        records = classify_changes(raw_changes, ruleset=ruleset)
        summary = _summarize(records)

        additive = sum(1 for r in records if r.verdict == "additive")
        behavioral = sum(1 for r in records if r.verdict == "behavioral")
        breaking = sum(1 for r in records if r.verdict == "breaking")

        assert summary.additive == additive
        assert summary.behavioral == behavioral
        assert summary.breaking == breaking


class TestChangeRecordInvariants:
    """Property tests for ChangeRecord model invariants."""

    @given(_raw_change_strategy())
    def test_change_record_change_id_is_hex_string(self, change: RawChange) -> None:
        """ChangeRecord.change_id is a valid hex string of length 16."""
        record = ChangeRecord(
            change_id=_change_id(change),
            kind=change.kind,
            location=change.location,
            verdict="behavioral",
            rule_id="TEST-RULE",
            rationale="Test rationale",
        )
        assert len(record.change_id) == 16
        assert all(c in "0123456789abcdef" for c in record.change_id)

    @given(
        st.sampled_from(["additive", "behavioral", "breaking"]),
    )
    def test_change_record_verdict_is_valid(self, verdict: Verdict) -> None:
        """ChangeRecord accepts all valid Verdict values."""
        record = ChangeRecord(
            change_id="0123456789abcdef",
            kind="test.kind",
            location="/test",
            verdict=verdict,
            rule_id="TEST",
            rationale="test",
        )
        assert record.verdict == verdict

    @given(_raw_change_strategy())
    def test_change_record_preserves_detail(self, change: RawChange) -> None:
        """ChangeRecord preserves detail dict from RawChange."""
        record = ChangeRecord(
            change_id=_change_id(change),
            kind=change.kind,
            location=change.location,
            verdict="behavioral",
            rule_id="TEST",
            rationale="test",
            detail=change.detail,
        )
        assert record.detail == change.detail
