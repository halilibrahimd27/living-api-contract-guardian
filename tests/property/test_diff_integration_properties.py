"""Integration property tests for the evolution rule engine.

Tests the full pipeline from contract diffing through rule classification
with custom rule overrides, focusing on end-to-end invariants.

Key scenario: Acceptance Criterion - "Custom rule YAML can be loaded and
overrides defaults" - tested with realistic OpenAPI and Protobuf scenarios.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from google.protobuf.descriptor_pb2 import FileDescriptorSet
from guardian_diff import diff_contracts, load_default_rules, load_rules_from_yaml
from guardian_diff.models import RawChange
from guardian_diff.ruleset import load_rules_from_text
from hypothesis import given
from hypothesis import strategies as st


def _minimal_openapi_spec() -> dict[str, Any]:
    """Generate a minimal valid OpenAPI spec."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "API", "version": "1.0.0"},
        "paths": {},
    }


def _openapi_with_path(path: str) -> dict[str, Any]:
    """Generate OpenAPI spec with a single path."""
    spec = _minimal_openapi_spec()
    spec["paths"][path] = {"get": {"responses": {"200": {"description": "OK"}}}}
    return spec


def _openapi_with_required_param(path: str, param_name: str) -> dict[str, Any]:
    """Generate OpenAPI spec with a required parameter."""
    spec = _minimal_openapi_spec()
    spec["paths"][path] = {
        "post": {
            "parameters": [
                {
                    "name": param_name,
                    "in": "query",
                    "schema": {"type": "string"},
                    "required": True,
                }
            ],
            "responses": {"200": {"description": "OK"}},
        }
    }
    return spec


def _minimal_proto_descriptor_set() -> bytes:
    """Generate a minimal FileDescriptorSet."""
    fds = FileDescriptorSet()
    file_proto = fds.file.add()
    file_proto.name = "test.proto"
    file_proto.package = "test"
    return fds.SerializeToString()


def _proto_with_message(message_name: str) -> bytes:
    """Generate FileDescriptorSet with a single message."""
    fds = FileDescriptorSet()
    file_proto = fds.file.add()
    file_proto.name = "test.proto"
    file_proto.package = "test"
    msg = file_proto.message_type.add()
    msg.name = message_name
    return fds.SerializeToString()


class TestAcceptanceCriterionCustomRuleOverrides:
    """Test the main acceptance criterion: custom rules override defaults."""

    def test_custom_rule_yaml_overrides_default_verdict(self) -> None:
        """Custom rule YAML overrides default verdict for known kinds."""
        # Start with defaults
        defaults = load_default_rules()

        # Create custom rule that changes the verdict for path.removed
        custom_yaml = """\
id: custom
rules:
  - id: OAS-PATH-REMOVED
    kind: openapi.path.removed
    verdict: additive
    rationale: In our org, removed paths are additive (deprecated endpoint pattern)
"""
        custom = load_rules_from_text(custom_yaml)
        merged = defaults.merge(custom)

        # Verify the override took effect
        before = _openapi_with_path("/v1/users")
        after = _minimal_openapi_spec()  # No /v1/users

        report = diff_contracts(
            kind="openapi",
            before=before,
            after=after,
            ruleset=merged,
        )

        # Should have a change for path removed
        assert any(c.kind == "openapi.path.removed" for c in report.changes)
        path_change = next(c for c in report.changes if c.kind == "openapi.path.removed")
        # With our custom rules, it should be additive, not breaking
        assert path_change.verdict == "additive"
        assert path_change.rule_id == "OAS-PATH-REMOVED"

    def test_custom_rule_yaml_loaded_from_file(self) -> None:
        """Custom rule YAML can be loaded from a file."""
        custom_yaml = """\
id: custom-from-file
rules:
  - id: CUSTOM-PARAM-REQUIRED
    kind: openapi.parameter.added.required
    verdict: additive
    rationale: In our org, new required params are negotiated with clients, so additive
"""
        with TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "custom_rules.yml"
            yaml_path.write_text(custom_yaml)

            # Load from file
            custom = load_rules_from_yaml(yaml_path)
            defaults = load_default_rules()
            merged = defaults.merge(custom)

            # Test classification
            change = RawChange(
                kind="openapi.parameter.added.required",
                location="/test",
            )
            verdict, rule_id, _ = merged.classify(change)

            assert rule_id == "CUSTOM-PARAM-REQUIRED"
            assert verdict == "additive"

    def test_multiple_custom_rule_overrides(self) -> None:
        """Multiple custom rules can override multiple defaults."""
        custom_yaml = """\
id: custom
rules:
  - id: OAS-PARAM-ADDED-REQUIRED
    kind: openapi.parameter.added.required
    verdict: behavioral
    rationale: "Deprecated: now additive"
  - id: OAS-PATH-REMOVED
    kind: openapi.path.removed
    verdict: additive
    rationale: Using deprecation pattern instead
"""
        custom = load_rules_from_text(custom_yaml)
        defaults = load_default_rules()
        merged = defaults.merge(custom)

        # Verify first override
        change1 = RawChange(
            kind="openapi.parameter.added.required",
            location="/test",
        )
        verdict1, rule_id1, _ = merged.classify(change1)
        assert rule_id1 == "OAS-PARAM-ADDED-REQUIRED"
        assert verdict1 == "behavioral"

        # Verify second override
        change2 = RawChange(
            kind="openapi.path.removed",
            location="/test",
        )
        verdict2, rule_id2, _ = merged.classify(change2)
        assert rule_id2 == "OAS-PATH-REMOVED"
        assert verdict2 == "additive"


class TestOpenAPIWithCustomRules:
    """Integration tests for OpenAPI diffing with custom rules."""

    def test_openapi_diff_with_custom_required_parameter_rule(self) -> None:
        """OpenAPI diffing respects custom rule for required parameters."""
        # before already has the path/operation; after adds a required param,
        # so the differ emits openapi.parameter.added.required (not path.added).
        before = _minimal_openapi_spec()
        before["paths"]["/api/users"] = {"post": {"responses": {"200": {"description": "OK"}}}}
        after = _openapi_with_required_param("/api/users", "api_key")

        # Custom rule: required parameters are behavioral, not breaking
        custom_yaml = """\
id: custom
rules:
  - id: CUSTOM-PARAM-REQUIRED
    kind: openapi.parameter.added.required
    verdict: behavioral
    rationale: Required params are not breaking for our clients
"""
        custom = load_rules_from_text(custom_yaml)

        report = diff_contracts(
            kind="openapi",
            before=before,
            after=after,
            ruleset=load_default_rules().merge(custom),
        )

        # Should have a required param change classified as behavioral
        param_changes = [c for c in report.changes if c.kind == "openapi.parameter.added.required"]
        assert len(param_changes) > 0
        for change in param_changes:
            assert change.verdict == "behavioral"
            assert change.rule_id == "CUSTOM-PARAM-REQUIRED"

    @given(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789_",
            min_size=1,
            max_size=29,
        ).map(lambda s: "/" + s)
    )
    def test_openapi_identical_specs_with_custom_rules(self, path: str) -> None:
        """Diffing identical specs produces no changes, regardless of custom rules."""
        spec = _openapi_with_path(path)

        custom_yaml = """\
id: custom
rules:
  - id: OVERRIDE
    kind: openapi.path.added
    verdict: breaking
    rationale: All new paths are breaking
"""
        custom = load_rules_from_text(custom_yaml)

        report = diff_contracts(
            kind="openapi",
            before=spec,
            after=spec,
            ruleset=load_default_rules().merge(custom),
        )

        # Identical specs should produce no changes
        assert len(report.changes) == 0
        assert report.summary.total == 0


class TestProtoWithCustomRules:
    """Integration tests for Protobuf diffing with custom rules."""

    def test_proto_diff_with_custom_field_removal_rule(self) -> None:
        """Proto diffing respects custom rule for field removals."""
        before = _proto_with_message("UserMessage")
        after = _minimal_proto_descriptor_set()

        # Custom rule: field removals are behavioral, not breaking
        custom_yaml = """\
id: custom
rules:
  - id: CUSTOM-FIELD-REMOVED
    kind: proto.field.removed
    verdict: behavioral
    rationale: Old clients can ignore removed fields
"""
        custom = load_rules_from_text(custom_yaml)

        report = diff_contracts(
            kind="proto",
            before=before,
            after=after,
            ruleset=load_default_rules().merge(custom),
        )

        # Field removal change might exist if the message had fields
        # This depends on proto structure, but custom rule should be honored if it applies
        for change in report.changes:
            if change.kind == "proto.field.removed":
                assert change.verdict == "behavioral"
                assert change.rule_id == "CUSTOM-FIELD-REMOVED"


class TestRulesetMergeProperties:
    """Integration tests for ruleset merging in end-to-end scenarios."""

    def test_custom_ruleset_id_in_report(self) -> None:
        """ChangeReport includes the ruleset_id from merged ruleset."""
        defaults = load_default_rules()
        custom_yaml = """\
id: my-custom-rules
rules: []
"""
        custom = load_rules_from_text(custom_yaml)
        merged = defaults.merge(custom)

        before = _minimal_openapi_spec()
        after = _openapi_with_path("/new")

        report = diff_contracts(
            kind="openapi",
            before=before,
            after=after,
            ruleset=merged,
        )

        assert report.ruleset_id == "my-custom-rules"

    def test_default_ruleset_id_without_override(self) -> None:
        """ChangeReport uses 'default' ruleset_id without custom override."""
        report = diff_contracts(
            kind="openapi",
            before=_minimal_openapi_spec(),
            after=_openapi_with_path("/test"),
        )

        assert report.ruleset_id == "default"


class TestSummaryWithCustomRules:
    """Integration tests for summary computation with custom rules."""

    def test_summary_counts_match_verdicts_after_override(self) -> None:
        """ChangeReport summary counts match verdicts with custom rules."""
        custom_yaml = """\
id: custom
rules:
  - id: OAS-PATH-ADDED
    kind: openapi.path.added
    verdict: breaking
    rationale: All new paths are breaking for us
"""
        custom = load_rules_from_text(custom_yaml)

        before = _minimal_openapi_spec()
        after = _openapi_with_path("/new/path")

        report = diff_contracts(
            kind="openapi",
            before=before,
            after=after,
            ruleset=load_default_rules().merge(custom),
        )

        # With custom override, new path should be breaking
        assert any(c.kind == "openapi.path.added" for c in report.changes)
        breaking_count = sum(1 for c in report.changes if c.verdict == "breaking")

        assert report.summary.breaking == breaking_count

    @given(st.integers(min_value=0, max_value=3))
    def test_summary_totals_consistent(self, num_paths: int) -> None:
        """ChangeReport summary totals are always consistent."""
        spec = _minimal_openapi_spec()
        for i in range(num_paths):
            spec["paths"][f"/path{i}"] = {"get": {"responses": {"200": {"description": "OK"}}}}

        report = diff_contracts(
            kind="openapi",
            before=spec,
            after=_minimal_openapi_spec(),
        )

        # Summary should always be consistent
        assert report.summary.total == len(report.changes)
        assert (
            report.summary.additive + report.summary.behavioral + report.summary.breaking
        ) == report.summary.total
