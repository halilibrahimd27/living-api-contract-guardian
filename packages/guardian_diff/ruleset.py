"""Rule loading and lookup for the evolution rule engine.

A rule maps a raw-change ``kind`` (optionally further constrained by a
location glob — a lightweight stand-in for the JSON Pointer selectors in
the milestone spec) to a verdict, a rule id, and a rationale string.
Rules are YAML-defined; the shipped default set lives at
``packages/guardian_diff/rules/default.yml``.

Custom rules override defaults by *id* (last-write-wins by id), so a
team can ship a project-local YAML that only contains the rules they
want to redefine.
"""

from __future__ import annotations

import fnmatch
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from guardian_diff.models import RawChange, Verdict


class Rule(BaseModel):
    """A single classification rule loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1, max_length=64)]
    kind: Annotated[str, Field(min_length=1, max_length=128)]
    verdict: Verdict
    rationale: Annotated[str, Field(min_length=1, max_length=2048)]
    location_glob: str | None = Field(
        default=None,
        description=(
            "Optional glob (fnmatch syntax) constraining the rule to changes whose "
            "location matches. Last matching rule wins, so more-specific globs "
            "should be listed after more-general ones."
        ),
    )

    def matches(self, change: RawChange) -> bool:
        if change.kind != self.kind:
            return False
        if self.location_glob is None:
            return True
        return fnmatch.fnmatchcase(change.location, self.location_glob)


class RuleSet(BaseModel):
    """An ordered collection of :class:`Rule` records.

    Lookup walks the rules in order and returns the *last* match (so
    custom rules appended after defaults override them). When no rule
    matches a given change, :meth:`classify` returns a fallback verdict
    of ``behavioral`` plus a synthetic ``UNCLASSIFIED`` rule id — the
    safe default for ambiguous changes that ought to be reviewed by a
    human.
    """

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1, max_length=64)] = "default"
    rules: list[Rule] = Field(default_factory=list)

    def classify(self, change: RawChange) -> tuple[Verdict, str, str]:
        match: Rule | None = None
        for rule in self.rules:
            if rule.matches(change):
                match = rule
        if match is None:
            return (
                "behavioral",
                "UNCLASSIFIED",
                f"No rule matched change kind {change.kind!r}; defaulting to behavioral.",
            )
        return match.verdict, match.id, match.rationale

    def merge(self, override: RuleSet) -> RuleSet:
        """Return a new RuleSet where rules from ``override`` replace by id.

        Rules in ``override`` whose id matches a rule in ``self`` replace
        the existing entry in-place (preserving order); new ids are
        appended.
        """
        by_id: dict[str, int] = {r.id: i for i, r in enumerate(self.rules)}
        merged: list[Rule] = list(self.rules)
        for rule in override.rules:
            if rule.id in by_id:
                merged[by_id[rule.id]] = rule
            else:
                merged.append(rule)
                by_id[rule.id] = len(merged) - 1
        return RuleSet(id=override.id or self.id, rules=merged)


def _parse_ruleset(data: Any, *, source: str) -> RuleSet:
    if not isinstance(data, dict):
        raise ValueError(f"rule YAML at {source!r} must be a mapping, got {type(data).__name__}")
    try:
        return RuleSet.model_validate(data)
    except ValidationError as exc:  # pragma: no cover - error message surface
        raise ValueError(f"invalid ruleset YAML at {source!r}: {exc}") from exc


def load_default_rules() -> RuleSet:
    """Load the in-package default ruleset."""
    text = resources.files("guardian_diff.rules").joinpath("default.yml").read_text()
    data = yaml.safe_load(text)
    return _parse_ruleset(data, source="packaged default.yml")


def load_rules_from_yaml(path: str | Path) -> RuleSet:
    """Load a user-supplied ruleset from disk."""
    p = Path(path)
    data = yaml.safe_load(p.read_text())
    return _parse_ruleset(data, source=str(p))


def load_rules_from_text(text: str, *, source: str = "<string>") -> RuleSet:
    """Load a ruleset from a raw YAML string (used by ``POST /diff``)."""
    data = yaml.safe_load(text)
    return _parse_ruleset(data, source=source)
