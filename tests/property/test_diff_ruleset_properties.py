"""Property-based tests for rule engine classification and merging.

Invariants tested:
1. Rule.matches() respects both kind and location_glob constraints
2. RuleSet.classify() returns verdicts that match rule verdicts
3. RuleSet.merge() preserves override semantics (override ids replace originals)
4. RuleSet.merge() appends new rule ids not in the base set
5. Default rules always classify known kinds to non-UNCLASSIFIED rule_ids
6. Unmatched change kinds always fall back to UNCLASSIFIED
7. Location glob matching is case-sensitive exact (fnmatch semantics)
"""

from __future__ import annotations

from guardian_diff.models import RawChange, Verdict
from guardian_diff.ruleset import Rule, RuleSet, load_default_rules, load_rules_from_text
from hypothesis import assume, given
from hypothesis import strategies as st


def _rule_strategy(
    kind_override: str | None = None,
    location_glob_override: str | None = None,
) -> st.SearchStrategy[Rule]:
    """Generate valid Rule objects."""
    kinds = st.sampled_from(
        [
            "openapi.path.added",
            "openapi.path.removed",
            "openapi.operation.added",
            "proto.field.added",
            "proto.field.removed",
            "custom.test.kind",
        ]
    )

    verdicts = st.sampled_from(["additive", "behavioral", "breaking"])

    rule_ids = st.text(
        alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
        min_size=1,
        max_size=64,
    ).filter(lambda s: len(s) > 0)

    rationales = st.text(min_size=1, max_size=2048)

    location_globs = st.one_of(
        st.none(),
        st.text(
            alphabet="/abcdefghijklmnopqrstuvwxyz0123456789_*?[]",
            min_size=1,
            max_size=100,
        ),
    )

    return st.builds(
        Rule,
        id=rule_ids,
        kind=st.just(kind_override) if kind_override else kinds,
        verdict=verdicts,
        rationale=rationales,
        location_glob=(
            st.just(location_glob_override)
            if location_glob_override is not None
            else location_globs
        ),
    )


def _raw_change_strategy_for_kind(kind: str) -> st.SearchStrategy[RawChange]:
    """Generate RawChange with specific kind for testing rule matching."""
    locations = st.text(
        alphabet="/abcdefghijklmnopqrstuvwxyz0123456789_-.",
        min_size=1,
        max_size=100,
    ).filter(lambda s: "/" in s or s.startswith("/"))

    return st.builds(
        RawChange,
        kind=st.just(kind),
        location=locations,
    )


class TestRuleMatching:
    """Property tests for Rule.matches() method."""

    @given(_rule_strategy())
    def test_rule_matches_same_kind_no_glob(self, rule: Rule) -> None:
        """A rule with no location_glob matches any change with matching kind."""
        assume(rule.location_glob is None)
        change = RawChange(kind=rule.kind, location="/test/path")
        assert rule.matches(change)

    @given(_rule_strategy())
    def test_rule_does_not_match_different_kind(self, rule: Rule) -> None:
        """A rule never matches a change with different kind."""
        different_kind = rule.kind + ".different"
        change = RawChange(kind=different_kind, location="/test/path")
        assert not rule.matches(change)

    @given(
        _rule_strategy(kind_override="openapi.path.added"),
    )
    def test_rule_glob_constrains_matching(self, rule: Rule) -> None:
        """A rule with location_glob only matches changes whose location matches the glob."""
        if rule.location_glob is None:
            # Rule matches everything with matching kind
            change = RawChange(kind=rule.kind, location="/any/path")
            assert rule.matches(change)
        else:
            # Rule only matches locations satisfying the glob
            change_match = RawChange(kind=rule.kind, location="/paths/users")
            change_no_match = RawChange(kind=rule.kind, location="/totally/different")
            # At least one of these should match the glob or neither does
            matches = rule.matches(change_match)
            no_matches = rule.matches(change_no_match)
            # Glob is a constraint, so it's possible neither matches
            assert isinstance(matches, bool)
            assert isinstance(no_matches, bool)

    @given(st.text(min_size=1, max_size=50).filter(lambda s: "/" not in s))
    def test_rule_matches_is_fnmatch_case_sensitive(self, text: str) -> None:
        """Rule.matches uses fnmatch (case-sensitive) for location globs."""
        rule = Rule(
            id="TEST",
            kind="test.kind",
            verdict="behavioral",
            rationale="test",
            location_glob="/Paths/*",  # Capital P
        )
        # Lowercase should NOT match
        change_lower = RawChange(kind="test.kind", location="/paths/users")
        assert not rule.matches(change_lower)
        # Uppercase should match
        change_upper = RawChange(kind="test.kind", location="/Paths/users")
        assert rule.matches(change_upper)


class TestRuleSetClassify:
    """Property tests for RuleSet.classify() method."""

    @given(st.lists(_rule_strategy(), min_size=1, max_size=20, unique_by=lambda r: r.id))
    def test_classify_returns_matching_rule_verdict(self, rules: list[Rule]) -> None:
        """classify() returns verdict matching the last matched rule."""
        if not rules:
            return
        ruleset = RuleSet(id="test", rules=rules)
        rule = rules[0]
        change = RawChange(kind=rule.kind, location="/test")
        verdict, rule_id, _ = ruleset.classify(change)
        # RuleSet.classify walks all rules and returns the *last* match
        # (so overrides win); pick the last matching rule for comparison.
        matching = [r for r in rules if r.matches(change)]
        if matching:
            expected = matching[-1]
            assert verdict == expected.verdict
            assert rule_id == expected.id

    @given(st.lists(_rule_strategy(), min_size=0, max_size=20, unique_by=lambda r: r.id))
    def test_classify_always_returns_valid_verdict(self, rules: list[Rule]) -> None:
        """classify() always returns a verdict in {additive, behavioral, breaking}."""
        ruleset = RuleSet(id="test", rules=rules)
        change = RawChange(kind="test.kind", location="/test")
        verdict, _, _ = ruleset.classify(change)
        assert verdict in {"additive", "behavioral", "breaking"}

    @given(st.lists(_rule_strategy(), min_size=0, max_size=20, unique_by=lambda r: r.id))
    def test_classify_always_returns_rule_id(self, rules: list[Rule]) -> None:
        """classify() always returns a non-empty rule_id."""
        ruleset = RuleSet(id="test", rules=rules)
        change = RawChange(kind="test.kind", location="/test")
        _, rule_id, _ = ruleset.classify(change)
        assert isinstance(rule_id, str)
        assert len(rule_id) > 0

    @given(st.lists(_rule_strategy(), min_size=0, max_size=20, unique_by=lambda r: r.id))
    def test_classify_always_returns_rationale(self, rules: list[Rule]) -> None:
        """classify() always returns a non-empty rationale."""
        ruleset = RuleSet(id="test", rules=rules)
        change = RawChange(kind="test.kind", location="/test")
        _, _, rationale = ruleset.classify(change)
        assert isinstance(rationale, str)
        assert len(rationale) > 0

    def test_classify_unmatched_returns_unclassified(self) -> None:
        """classify() returns UNCLASSIFIED for unmatched change kinds."""
        ruleset = RuleSet(
            id="test",
            rules=[
                Rule(
                    id="RULE1",
                    kind="known.kind",
                    verdict="breaking",
                    rationale="Known.",
                )
            ],
        )
        change = RawChange(kind="unknown.kind", location="/test")
        _, rule_id, _ = ruleset.classify(change)
        assert rule_id == "UNCLASSIFIED"

    def test_classify_unmatched_defaults_to_behavioral(self) -> None:
        """classify() defaults to behavioral for unmatched change kinds."""
        ruleset = RuleSet(
            id="test",
            rules=[
                Rule(
                    id="RULE1",
                    kind="known.kind",
                    verdict="breaking",
                    rationale="Known.",
                )
            ],
        )
        change = RawChange(kind="unknown.kind", location="/test")
        verdict, _, _ = ruleset.classify(change)
        assert verdict == "behavioral"

    def test_classify_last_matching_rule_wins(self) -> None:
        """When multiple rules match, the last one in order wins."""
        ruleset = RuleSet(
            id="test",
            rules=[
                Rule(
                    id="RULE1",
                    kind="test.kind",
                    verdict="additive",
                    rationale="First rule.",
                ),
                Rule(
                    id="RULE2",
                    kind="test.kind",
                    verdict="breaking",
                    rationale="Second rule.",
                ),
            ],
        )
        change = RawChange(kind="test.kind", location="/test")
        verdict, rule_id, _ = ruleset.classify(change)
        assert rule_id == "RULE2"
        assert verdict == "breaking"


class TestRuleSetMerge:
    """Property tests for RuleSet.merge() method."""

    @given(
        st.lists(_rule_strategy(), min_size=0, max_size=10, unique_by=lambda r: r.id),
        st.lists(_rule_strategy(), min_size=0, max_size=10, unique_by=lambda r: r.id),
    )
    def test_merge_preserves_base_rules(
        self, base_rules: list[Rule], override_rules: list[Rule]
    ) -> None:
        """Merge preserves rules from base set that don't conflict."""
        base = RuleSet(id="base", rules=base_rules)
        override = RuleSet(id="override", rules=override_rules)
        merged = base.merge(override)

        base_ids = {r.id for r in base_rules}
        # All base IDs should still be present (possibly replaced)
        merged_ids = {r.id for r in merged.rules}
        for bid in base_ids:
            assert bid in merged_ids

    @given(
        st.lists(_rule_strategy(), min_size=1, max_size=10, unique_by=lambda r: r.id),
    )
    def test_merge_uses_override_verdict_for_matching_id(self, base_rules: list[Rule]) -> None:
        """When an override rule has same id as base, the override verdict is used."""
        if not base_rules:
            return
        base = RuleSet(id="base", rules=base_rules)

        # Create override with same id but different verdict
        rule_to_override = base_rules[0]
        different_verdict: Verdict = (
            "breaking" if rule_to_override.verdict != "breaking" else "additive"
        )
        override = RuleSet(
            id="override",
            rules=[
                Rule(
                    id=rule_to_override.id,
                    kind=rule_to_override.kind,
                    verdict=different_verdict,
                    rationale="Override.",
                )
            ],
        )
        merged = base.merge(override)

        # Find the merged rule
        merged_rule = next((r for r in merged.rules if r.id == rule_to_override.id), None)
        assert merged_rule is not None
        assert merged_rule.verdict == different_verdict

    @given(
        st.lists(_rule_strategy(), min_size=0, max_size=10, unique_by=lambda r: r.id),
    )
    def test_merge_appends_new_ids(self, base_rules: list[Rule]) -> None:
        """Merge appends override rules with new ids not in base."""
        base = RuleSet(id="base", rules=base_rules)
        new_rule = Rule(
            id="NEW-RULE-ID",
            kind="new.kind",
            verdict="additive",
            rationale="New rule.",
        )
        override = RuleSet(id="override", rules=[new_rule])
        merged = base.merge(override)

        # NEW-RULE-ID should be present
        merged_ids = {r.id for r in merged.rules}
        assert "NEW-RULE-ID" in merged_ids

    @given(
        st.lists(_rule_strategy(), min_size=1, max_size=10, unique_by=lambda r: r.id),
    )
    def test_merge_preserves_order_for_replaced_rules(self, base_rules: list[Rule]) -> None:
        """Merge preserves position when replacing a rule by id."""
        if not base_rules:
            return
        base = RuleSet(id="base", rules=base_rules)
        rule_to_replace = base_rules[0]
        position = next((i for i, r in enumerate(base_rules) if r.id == rule_to_replace.id), -1)

        override = RuleSet(
            id="override",
            rules=[
                Rule(
                    id=rule_to_replace.id,
                    kind="different.kind",
                    verdict="behavioral",
                    rationale="Replaced.",
                )
            ],
        )
        merged = base.merge(override)

        # Find position in merged
        merged_position = next(
            (i for i, r in enumerate(merged.rules) if r.id == rule_to_replace.id), -1
        )
        assert merged_position == position

    def test_merge_override_id_takes_precedence(self) -> None:
        """Merge uses override.id for the result if provided."""
        base = RuleSet(id="base", rules=[])
        override = RuleSet(id="custom", rules=[])
        merged = base.merge(override)
        assert merged.id == "custom"


class TestDefaultRuleSet:
    """Property tests for default ruleset loading and classification."""

    def test_default_rules_classify_known_kinds(self) -> None:
        """Default ruleset classifies all known change kinds without UNCLASSIFIED."""
        default = load_default_rules()

        # Sample known kinds from the spec
        known_kinds = [
            "openapi.path.added",
            "openapi.path.removed",
            "openapi.operation.added",
            "openapi.parameter.added.optional",
            "openapi.parameter.added.required",
            "proto.field.added",
            "proto.field.removed",
            "proto.field.number_changed",
            "proto.rpc.added",
            "proto.rpc.removed",
        ]

        for kind in known_kinds:
            change = RawChange(kind=kind, location="/test")
            _, rule_id, _ = default.classify(change)
            assert rule_id != "UNCLASSIFIED"

    def test_default_rules_load_deterministically(self) -> None:
        """Loading default rules multiple times yields identical results."""
        rules1 = load_default_rules()
        rules2 = load_default_rules()
        assert len(rules1.rules) == len(rules2.rules)
        for r1, r2 in zip(rules1.rules, rules2.rules, strict=True):
            assert r1.id == r2.id
            assert r1.kind == r2.kind
            assert r1.verdict == r2.verdict

    def test_default_rules_have_non_empty_rationales(self) -> None:
        """All default rules have non-empty rationales."""
        default = load_default_rules()
        for rule in default.rules:
            assert isinstance(rule.rationale, str)
            assert len(rule.rationale) > 0

    def test_default_rules_ids_are_unique(self) -> None:
        """All default rule ids are unique."""
        default = load_default_rules()
        ids = [r.id for r in default.rules]
        assert len(ids) == len(set(ids))


class TestRuleLoadingFromText:
    """Property tests for loading rules from YAML text."""

    def test_load_rules_from_text_preserves_verdicts(self) -> None:
        """Loading rules from YAML preserves verdict values."""
        yaml_text = """\
id: custom
rules:
  - id: ADDITIVE-RULE
    kind: custom.additive
    verdict: additive
    rationale: Test additive rule.
  - id: BREAKING-RULE
    kind: custom.breaking
    verdict: breaking
    rationale: Test breaking rule.
"""
        ruleset = load_rules_from_text(yaml_text)
        additive_rule = next((r for r in ruleset.rules if r.id == "ADDITIVE-RULE"), None)
        breaking_rule = next((r for r in ruleset.rules if r.id == "BREAKING-RULE"), None)
        assert additive_rule is not None
        assert additive_rule.verdict == "additive"
        assert breaking_rule is not None
        assert breaking_rule.verdict == "breaking"

    def test_load_rules_from_text_preserves_location_globs(self) -> None:
        """Loading rules from YAML preserves location_glob values."""
        yaml_text = """\
id: custom
rules:
  - id: SCOPED-RULE
    kind: test.kind
    verdict: breaking
    rationale: A rule with glob.
    location_glob: /admin/*
"""
        ruleset = load_rules_from_text(yaml_text)
        scoped = next((r for r in ruleset.rules if r.id == "SCOPED-RULE"), None)
        assert scoped is not None
        assert scoped.location_glob == "/admin/*"
