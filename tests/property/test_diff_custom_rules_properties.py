"""Property-based tests for custom rule loading and override semantics.

Acceptance criterion: Custom rule YAML can be loaded and overrides defaults.

Invariants tested:
1. Custom rules loaded from YAML merge correctly into default rules
2. Custom rules with same id as defaults override them exactly
3. Custom rules with new ids are appended after defaults
4. Override operation preserves order of base rules
5. Multiple custom rules can be composed (merge is associative for outcome)
6. Default rules are always loaded deterministically
7. Custom rules with valid YAML structure are parsed correctly
8. Classification with merged rulesets uses the overridden rules
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml
from guardian_diff import load_default_rules, load_rules_from_yaml
from guardian_diff.models import RawChange, Verdict
from guardian_diff.ruleset import Rule, RuleSet, load_rules_from_text
from hypothesis import assume, given
from hypothesis import strategies as st


def _rule_strategy(min_size: int = 1, max_size: int = 10) -> st.SearchStrategy[Rule]:
    """Generate valid Rule objects for custom rulesets."""
    kinds = st.sampled_from(
        [
            "custom.additive.type",
            "custom.behavioral.type",
            "custom.breaking.type",
            "test.override.kind",
        ]
    )

    verdicts = st.sampled_from(["additive", "behavioral", "breaking"])

    rule_ids = st.text(
        alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
        min_size=1,
        max_size=32,
    ).filter(lambda s: len(s) > 0)

    rationales = st.text(min_size=5, max_size=500)

    location_globs = st.one_of(
        st.none(),
        st.text(
            alphabet="/abcdefghijklmnopqrstuvwxyz0123456789_*?",
            min_size=3,
            max_size=50,
        ),
    )

    return st.builds(
        Rule,
        id=rule_ids,
        kind=kinds,
        verdict=verdicts,
        rationale=rationales,
        location_glob=location_globs,
    )


def _custom_ruleset_yaml(rules: list[Rule]) -> str:
    """Convert a list of Rule objects to YAML string."""
    rules_data = [
        {
            "id": r.id,
            "kind": r.kind,
            "verdict": r.verdict,
            "rationale": r.rationale,
            **({"location_glob": r.location_glob} if r.location_glob else {}),
        }
        for r in rules
    ]
    data = {"id": "custom", "rules": rules_data}
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


class TestLoadRulesFromYaml:
    """Property tests for load_rules_from_yaml function."""

    @given(st.lists(_rule_strategy(), min_size=1, max_size=5, unique_by=lambda r: r.id))
    def test_load_yaml_preserves_rule_count(self, rules: list[Rule]) -> None:
        """Loading custom rules from YAML preserves the rule count."""
        yaml_str = _custom_ruleset_yaml(rules)

        with TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "custom_rules.yml"
            yaml_path.write_text(yaml_str)
            loaded = load_rules_from_yaml(yaml_path)

            assert len(loaded.rules) == len(rules)

    @given(st.lists(_rule_strategy(), min_size=1, max_size=5, unique_by=lambda r: r.id))
    def test_load_yaml_preserves_rule_ids(self, rules: list[Rule]) -> None:
        """Loading custom rules from YAML preserves rule ids."""
        yaml_str = _custom_ruleset_yaml(rules)
        original_ids = {r.id for r in rules}

        with TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "custom_rules.yml"
            yaml_path.write_text(yaml_str)
            loaded = load_rules_from_yaml(yaml_path)
            loaded_ids = {r.id for r in loaded.rules}

            assert loaded_ids == original_ids

    @given(st.lists(_rule_strategy(), min_size=1, max_size=5, unique_by=lambda r: r.id))
    def test_load_yaml_preserves_verdicts(self, rules: list[Rule]) -> None:
        """Loading custom rules from YAML preserves verdicts."""
        yaml_str = _custom_ruleset_yaml(rules)
        verdict_map = {r.id: r.verdict for r in rules}

        with TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "custom_rules.yml"
            yaml_path.write_text(yaml_str)
            loaded = load_rules_from_yaml(yaml_path)

            for rule in loaded.rules:
                assert rule.verdict == verdict_map[rule.id]

    @given(st.lists(_rule_strategy(), min_size=1, max_size=5, unique_by=lambda r: r.id))
    def test_load_yaml_preserves_rationales(self, rules: list[Rule]) -> None:
        """Loading custom rules from YAML preserves rationales."""
        yaml_str = _custom_ruleset_yaml(rules)
        rationale_map = {r.id: r.rationale for r in rules}

        with TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "custom_rules.yml"
            yaml_path.write_text(yaml_str)
            loaded = load_rules_from_yaml(yaml_path)

            for rule in loaded.rules:
                assert rule.rationale == rationale_map[rule.id]

    @given(st.lists(_rule_strategy(), min_size=1, max_size=5, unique_by=lambda r: r.id))
    def test_load_yaml_preserves_location_globs(self, rules: list[Rule]) -> None:
        """Loading custom rules from YAML preserves location_glob values."""
        yaml_str = _custom_ruleset_yaml(rules)
        glob_map = {r.id: r.location_glob for r in rules}

        with TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "custom_rules.yml"
            yaml_path.write_text(yaml_str)
            loaded = load_rules_from_yaml(yaml_path)

            for rule in loaded.rules:
                assert rule.location_glob == glob_map[rule.id]


class TestCustomRuleOverrides:
    """Property tests for custom rule override semantics."""

    def test_custom_rules_override_defaults_by_id(self) -> None:
        """Custom rules override default rules when same id is used."""
        defaults = load_default_rules()

        # Pick a default rule and override it
        if not defaults.rules:
            pytest.skip("No default rules to override")

        rule_to_override = defaults.rules[0]

        # Create a custom rule with the same id but different verdict
        override_yaml = f"""\
id: custom
rules:
  - id: {rule_to_override.id}
    kind: {rule_to_override.kind}
    verdict: behavioral
    rationale: Overridden rationale
"""
        custom = load_rules_from_text(override_yaml)
        merged = defaults.merge(custom)

        # Find the merged rule
        merged_rule = next((r for r in merged.rules if r.id == rule_to_override.id), None)
        assert merged_rule is not None
        assert merged_rule.verdict == "behavioral"
        assert merged_rule.rationale == "Overridden rationale"

    @given(st.lists(_rule_strategy(), min_size=1, max_size=3, unique_by=lambda r: r.id))
    def test_custom_rules_append_new_ids(self, custom_rules: list[Rule]) -> None:
        """Custom rules with new ids are appended to the ruleset."""
        defaults = load_default_rules()
        default_ids = {r.id for r in defaults.rules}

        # Ensure our custom rules don't conflict with defaults
        assume(not any(r.id in default_ids for r in custom_rules))

        yaml_str = _custom_ruleset_yaml(custom_rules)
        custom = load_rules_from_text(yaml_str)
        merged = defaults.merge(custom)

        # All new ids should be present
        merged_ids = {r.id for r in merged.rules}
        for rule in custom_rules:
            assert rule.id in merged_ids

    @given(st.lists(_rule_strategy(), min_size=1, max_size=3, unique_by=lambda r: r.id))
    def test_merged_rules_classify_with_custom_override(self, custom_rules: list[Rule]) -> None:
        """Classification uses custom rules when merged with defaults."""
        defaults = load_default_rules()
        yaml_str = _custom_ruleset_yaml(custom_rules)
        custom = load_rules_from_text(yaml_str)
        merged = defaults.merge(custom)

        # For each custom rule, verify it's used in classification
        for custom_rule in custom_rules:
            change = RawChange(kind=custom_rule.kind, location="/test")
            verdict, rule_id, _ = merged.classify(change)

            # If the change matches this custom rule (or a later one), verdict should be consistent
            matching_rules = [r for r in merged.rules if r.matches(change)]
            if matching_rules:
                expected = matching_rules[-1]
                assert verdict == expected.verdict
                assert rule_id == expected.id

    def test_custom_rules_override_default_classification(self) -> None:
        """Custom rules change the classification outcome for default kinds."""
        defaults = load_default_rules()

        # Create a custom rule that overrides a default classification
        override_yaml = """\
id: custom
rules:
  - id: OVERRIDE-PATH-ADDED
    kind: openapi.path.added
    verdict: breaking
    rationale: "Override: paths are now breaking"
"""
        custom = load_rules_from_text(override_yaml)
        merged = defaults.merge(custom)

        # Classify a path addition change
        change = RawChange(kind="openapi.path.added", location="/new/path")
        verdict, rule_id, _ = merged.classify(change)

        # Should use the override
        assert rule_id == "OVERRIDE-PATH-ADDED"
        assert verdict == "breaking"

    @given(st.lists(_rule_strategy(), min_size=1, max_size=2, unique_by=lambda r: r.id))
    def test_multiple_custom_overrides_compose(self, custom_rules: list[Rule]) -> None:
        """Multiple custom ruleset merges compose correctly."""
        defaults = load_default_rules()
        assume(len(custom_rules) >= 2)

        # Create two separate custom rulesets
        yaml1 = _custom_ruleset_yaml([custom_rules[0]])
        yaml2 = _custom_ruleset_yaml([custom_rules[1]])

        custom1 = load_rules_from_text(yaml1)
        custom2 = load_rules_from_text(yaml2)

        # Merge both ways
        merged_1_then_2 = defaults.merge(custom1).merge(custom2)
        merged_both = defaults.merge(RuleSet(id="combined", rules=custom1.rules + custom2.rules))

        # Both should have all the same rules
        ids_1_2 = {r.id for r in merged_1_then_2.rules}
        ids_both = {r.id for r in merged_both.rules}

        assert ids_1_2 == ids_both


class TestCustomRuleVerdicts:
    """Property tests for verdict values in custom rules."""

    @given(st.sampled_from(["additive", "behavioral", "breaking"]))
    def test_custom_rule_any_verdict_is_valid(self, verdict: Verdict) -> None:
        """Custom rules can assign any valid verdict."""
        yaml_str = f"""\
id: custom
rules:
  - id: TEST-RULE
    kind: test.kind
    verdict: {verdict}
    rationale: Test
"""
        custom = load_rules_from_text(yaml_str)
        assert len(custom.rules) == 1
        assert custom.rules[0].verdict == verdict

    @given(
        st.lists(
            st.tuples(
                st.text(alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=3, max_size=20),
                st.sampled_from(["additive", "behavioral", "breaking"]),
            ),
            min_size=1,
            max_size=5,
            unique_by=lambda x: x[0],
        )
    )
    def test_custom_rules_all_verdicts_honored(
        self, kind_verdict_pairs: list[tuple[str, str]]
    ) -> None:
        """All verdicts in custom rules are honored in classification."""
        rules_data = [
            {
                "id": f"RULE-{idx}",
                "kind": kind,
                "verdict": verdict,
                "rationale": f"Rule for {kind}",
            }
            for idx, (kind, verdict) in enumerate(kind_verdict_pairs)
        ]

        yaml_str = yaml.dump({"id": "custom", "rules": rules_data})
        custom = load_rules_from_text(yaml_str)

        for _idx, (kind, expected_verdict) in enumerate(kind_verdict_pairs):
            change = RawChange(kind=kind, location="/test")
            verdict, _, _ = custom.classify(change)
            assert verdict == expected_verdict


class TestDefaultRulesConsistency:
    """Property tests for default ruleset consistency."""

    def test_default_rules_always_load_identically(self) -> None:
        """Default rules load identically across multiple invocations."""
        defaults1 = load_default_rules()
        defaults2 = load_default_rules()
        defaults3 = load_default_rules()

        ids1 = [(r.id, r.kind, r.verdict) for r in defaults1.rules]
        ids2 = [(r.id, r.kind, r.verdict) for r in defaults2.rules]
        ids3 = [(r.id, r.kind, r.verdict) for r in defaults3.rules]

        assert ids1 == ids2 == ids3

    def test_default_rules_have_expected_coverage(self) -> None:
        """Default rules cover known change kinds from OpenAPI and Protobuf."""
        defaults = load_default_rules()
        default_kinds = {r.kind for r in defaults.rules}

        expected_kinds = {
            "openapi.path.added",
            "openapi.path.removed",
            "openapi.operation.added",
            "openapi.parameter.added.optional",
            "openapi.parameter.added.required",
            "proto.field.added",
            "proto.field.removed",
            "proto.field.number_changed",
            "proto.message.added",
        }

        for kind in expected_kinds:
            assert kind in default_kinds

    def test_default_rules_have_unique_ids(self) -> None:
        """Default ruleset has no duplicate rule ids."""
        defaults = load_default_rules()
        ids = [r.id for r in defaults.rules]
        assert len(ids) == len(set(ids))


class TestCustomRuleEdgeCases:
    """Property tests for edge cases in custom rule loading."""

    def test_empty_custom_ruleset_merges_correctly(self) -> None:
        """Empty custom ruleset merges without affecting defaults."""
        defaults = load_default_rules()
        custom = RuleSet(id="empty", rules=[])
        merged = defaults.merge(custom)

        assert len(merged.rules) == len(defaults.rules)
        default_ids = {r.id for r in defaults.rules}
        merged_ids = {r.id for r in merged.rules}
        assert default_ids == merged_ids

    @given(
        st.lists(
            _rule_strategy(),
            min_size=1,
            max_size=3,
            unique_by=lambda r: r.id,
        )
    )
    def test_ruleset_id_updated_on_merge(self, custom_rules: list[Rule]) -> None:
        """Merged ruleset takes the override ruleset's id."""
        defaults = load_default_rules()
        custom_id = "my-custom-ruleset"
        custom = RuleSet(id=custom_id, rules=custom_rules)
        merged = defaults.merge(custom)

        assert merged.id == custom_id

    def test_rule_order_preserved_for_classification(self) -> None:
        """Rule order is preserved after merge, last match wins."""
        rule1 = Rule(id="R1", kind="test.kind", verdict="additive", rationale="First")
        rule2 = Rule(id="R2", kind="test.kind", verdict="breaking", rationale="Second")

        defaults = RuleSet(id="defaults", rules=[rule1])
        custom = RuleSet(id="custom", rules=[rule2])
        merged = defaults.merge(custom)

        # Both rules match "test.kind", last one should win
        change = RawChange(kind="test.kind", location="/test")
        verdict, rule_id, _ = merged.classify(change)

        assert rule_id == "R2"
        assert verdict == "breaking"


class TestIntegrationWithDefaultRules:
    """Integration tests combining custom rules with default behavior."""

    def test_custom_rules_integrate_with_default_classification(self) -> None:
        """Custom and default rules work together in classification."""
        custom_yaml = """\
id: custom
rules:
  - id: CUSTOM-PARAM-REQUIRED
    kind: openapi.parameter.added.required
    verdict: behavioral
    rationale: "Custom: required params are behavioral, not breaking"
"""
        defaults = load_default_rules()
        custom = load_rules_from_text(custom_yaml)
        merged = defaults.merge(custom)

        # The custom rule should override the default for required params
        change = RawChange(kind="openapi.parameter.added.required", location="/test")
        verdict, rule_id, _ = merged.classify(change)

        assert rule_id == "CUSTOM-PARAM-REQUIRED"
        assert verdict == "behavioral"

    def test_unmatched_custom_rules_dont_affect_default_kinds(self) -> None:
        """Custom rules for unknown kinds don't break defaults."""
        custom_yaml = """\
id: custom
rules:
  - id: UNKNOWN-RULE
    kind: custom.unknown.kind
    verdict: additive
    rationale: Unknown
"""
        defaults = load_default_rules()
        custom = load_rules_from_text(custom_yaml)
        merged = defaults.merge(custom)

        # A known kind should still classify correctly
        change = RawChange(kind="openapi.path.added", location="/test")
        verdict, rule_id, _ = merged.classify(change)

        assert verdict == "additive"
        assert rule_id == "OAS-PATH-ADDED"
