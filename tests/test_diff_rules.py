"""Custom YAML rule loading + override semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from guardian_diff import diff_contracts, load_default_rules, load_rules_from_yaml
from guardian_diff.models import RawChange
from guardian_diff.ruleset import RuleSet, load_rules_from_text


def _spec() -> dict[str, Any]:
    return {"openapi": "3.0.0", "info": {"title": "x", "version": "1"}, "paths": {}}


def test_default_rules_load_with_expected_size() -> None:
    rs = load_default_rules()
    assert rs.id == "default"
    # We ship rules covering both contract families; sanity-check we have a
    # non-trivial number, and that all our well-known ids exist.
    ids = {r.id for r in rs.rules}
    for expected in {
        "OAS-PATH-REMOVED",
        "OAS-OP-ADDED",
        "OAS-PARAM-ADDED-REQUIRED",
        "PROTO-FIELD-NUMBER-CHANGED",
        "PROTO-RPC-RESPONSE-TYPE-CHANGED",
    }:
        assert expected in ids


def test_custom_yaml_overrides_default_verdict(tmp_path: Path) -> None:
    yml = tmp_path / "custom.yml"
    yml.write_text("""\
id: strict
rules:
  - id: OAS-RESPONSE-ADDED
    kind: openapi.response.added
    verdict: breaking
    rationale: Strict mode treats new response codes as a contract change.
""")
    custom = load_rules_from_yaml(yml)
    merged = load_default_rules().merge(custom)
    # The default verdict for ``openapi.response.added`` is ``behavioral``;
    # our override should flip it.
    change = RawChange(
        kind="openapi.response.added",
        location="/paths//u/get/responses/418",
    )
    verdict, rule_id, _ = merged.classify(change)
    assert verdict == "breaking"
    assert rule_id == "OAS-RESPONSE-ADDED"
    # The default ruleset itself is unchanged.
    default_verdict, _, _ = load_default_rules().classify(change)
    assert default_verdict == "behavioral"


def test_custom_yaml_can_add_new_rule() -> None:
    custom = load_rules_from_text("""\
id: extra
rules:
  - id: CUSTOM-ONE
    kind: openapi.custom.kind
    verdict: breaking
    rationale: A made-up change kind handled by a project-specific rule.
""")
    merged = load_default_rules().merge(custom)
    change = RawChange(kind="openapi.custom.kind", location="/x")
    verdict, rule_id, _ = merged.classify(change)
    assert verdict == "breaking"
    assert rule_id == "CUSTOM-ONE"


def test_unmatched_change_kind_falls_back_to_behavioral() -> None:
    rs = load_default_rules()
    verdict, rule_id, _ = rs.classify(RawChange(kind="openapi.nonexistent", location="/x"))
    assert verdict == "behavioral"
    assert rule_id == "UNCLASSIFIED"


def test_diff_contracts_honors_custom_ruleset() -> None:
    custom = load_rules_from_text("""\
id: strict
rules:
  - id: OAS-RESPONSE-ADDED
    kind: openapi.response.added
    verdict: breaking
    rationale: Strict override.
""")
    merged = load_default_rules().merge(custom)

    before = _spec()
    before["paths"]["/u"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    after = _spec()
    after["paths"]["/u"] = {
        "get": {
            "responses": {
                "200": {"description": "ok"},
                "418": {"description": "teapot"},
            }
        }
    }
    default_report = diff_contracts(kind="openapi", before=before, after=after)
    strict_report = diff_contracts(kind="openapi", before=before, after=after, ruleset=merged)
    assert default_report.summary.behavioral == 1
    assert default_report.summary.breaking == 0
    assert strict_report.summary.breaking == 1
    assert strict_report.ruleset_id == "strict"


def test_location_glob_scopes_a_rule() -> None:
    rs = RuleSet(
        id="scoped",
        rules=[
            *load_default_rules().rules,
            {  # type: ignore[list-item]
                "id": "ADMIN-STRICT",
                "kind": "openapi.path.added",
                "verdict": "breaking",
                "rationale": "Admin paths must never be added in a minor release.",
                "location_glob": "/paths/*/admin/*",
            },
        ],
    )
    # Catch-all rule still classifies an ordinary path-added as additive.
    ordinary = RawChange(kind="openapi.path.added", location="/paths//users")
    assert rs.classify(ordinary)[0] == "additive"
    # The narrower rule wins (last match) when location matches.
    admin = RawChange(kind="openapi.path.added", location="/paths//admin/reset")
    verdict, rule_id, _ = rs.classify(admin)
    assert verdict == "breaking"
    assert rule_id == "ADMIN-STRICT"


def test_invalid_yaml_rejected() -> None:
    with pytest.raises(ValueError):
        load_rules_from_text("not a mapping")
    with pytest.raises(ValueError):
        load_rules_from_text("rules: [{id: x, kind: y, verdict: not-a-verdict, rationale: r}]")
