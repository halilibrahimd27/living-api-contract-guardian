"""Breaking-change fixture matrix for the OpenAPI rule engine.

Each case shapes a minimal ``before`` / ``after`` OpenAPI spec, drives
it through :func:`guardian_diff.diff_contracts`, and asserts both the
expected ``kind`` is present in the report AND the verdict matches the
expected classification.
"""

from __future__ import annotations

from typing import Any

import pytest
from guardian_diff import diff_contracts
from guardian_diff.models import ChangeReport


def _shell() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {"title": "demo", "version": "1.0.0"},
        "paths": {},
        "components": {"schemas": {}},
    }


def _make_op(**kwargs: Any) -> dict[str, Any]:
    op = {"responses": {"200": {"description": "ok"}}}
    op.update(kwargs)
    return op


def _by_kind(report: ChangeReport, kind: str) -> list[object]:
    return [c for c in report.changes if c.kind == kind]


def _run(before: dict[str, Any], after: dict[str, Any]) -> ChangeReport:
    return diff_contracts(kind="openapi", before=before, after=after)


# ---------- additive ----------


def test_path_added_is_additive() -> None:
    before = _shell()
    after = _shell()
    after["paths"]["/users"] = {"get": _make_op()}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.path.added")
    assert len(hits) == 1
    assert hits[0].verdict == "additive"
    assert hits[0].rule_id == "OAS-PATH-ADDED"


def test_operation_added_is_additive() -> None:
    before = _shell()
    before["paths"]["/users"] = {"get": _make_op()}
    after = _shell()
    after["paths"]["/users"] = {"get": _make_op(), "post": _make_op()}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.operation.added")
    assert len(hits) == 1
    assert hits[0].verdict == "additive"


def test_optional_parameter_added_is_additive() -> None:
    before = _shell()
    before["paths"]["/users"] = {"get": _make_op(parameters=[])}
    after = _shell()
    after["paths"]["/users"] = {
        "get": _make_op(
            parameters=[{"in": "query", "name": "limit", "schema": {"type": "integer"}}]
        )
    }
    report = _run(before, after)
    hits = _by_kind(report, "openapi.parameter.added.optional")
    assert len(hits) == 1 and hits[0].verdict == "additive"


def test_optional_body_property_added_is_additive() -> None:
    before_op = _make_op(
        requestBody={
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                        "required": ["a"],
                    }
                }
            }
        }
    )
    after_op = _make_op(
        requestBody={
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                        "required": ["a"],
                    }
                }
            }
        }
    )
    before = _shell()
    before["paths"]["/users"] = {"post": before_op}
    after = _shell()
    after["paths"]["/users"] = {"post": after_op}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.request_body.property.added.optional")
    assert len(hits) == 1 and hits[0].verdict == "additive"


def test_response_property_added_is_additive() -> None:
    before_resp = {
        "200": {
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                }
            }
        }
    }
    after_resp = {
        "200": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                    }
                }
            }
        }
    }
    before = _shell()
    before["paths"]["/u"] = {"get": {"responses": before_resp}}
    after = _shell()
    after["paths"]["/u"] = {"get": {"responses": after_resp}}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.response.property.added")
    assert len(hits) == 1 and hits[0].verdict == "additive"


def test_required_parameter_decreased_is_additive() -> None:
    before = _shell()
    before["paths"]["/u"] = {
        "get": _make_op(
            parameters=[
                {"in": "query", "name": "q", "required": True, "schema": {"type": "string"}}
            ]
        )
    }
    after = _shell()
    after["paths"]["/u"] = {
        "get": _make_op(
            parameters=[
                {"in": "query", "name": "q", "required": False, "schema": {"type": "string"}}
            ]
        )
    }
    report = _run(before, after)
    hits = _by_kind(report, "openapi.parameter.required.decreased")
    assert len(hits) == 1 and hits[0].verdict == "additive"


# ---------- behavioral ----------


def test_response_added_is_behavioral() -> None:
    before_op = {"responses": {"200": {"description": "ok"}}}
    after_op = {"responses": {"200": {"description": "ok"}, "418": {"description": "teapot"}}}
    before = _shell()
    before["paths"]["/u"] = {"get": before_op}
    after = _shell()
    after["paths"]["/u"] = {"get": after_op}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.response.added")
    assert len(hits) == 1 and hits[0].verdict == "behavioral"


def test_enum_value_added_is_behavioral() -> None:
    before = _shell()
    before["components"]["schemas"]["Status"] = {"type": "string", "enum": ["a", "b"]}
    after = _shell()
    after["components"]["schemas"]["Status"] = {"type": "string", "enum": ["a", "b", "c"]}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.enum.value.added")
    assert len(hits) == 1 and hits[0].verdict == "behavioral"


def test_parameter_removed_is_behavioral() -> None:
    before = _shell()
    before["paths"]["/u"] = {
        "get": _make_op(parameters=[{"in": "query", "name": "old", "schema": {"type": "string"}}])
    }
    after = _shell()
    after["paths"]["/u"] = {"get": _make_op(parameters=[])}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.parameter.removed")
    assert len(hits) == 1 and hits[0].verdict == "behavioral"


# ---------- breaking ----------


def test_path_removed_is_breaking() -> None:
    before = _shell()
    before["paths"]["/users"] = {"get": _make_op()}
    after = _shell()
    report = _run(before, after)
    hits = _by_kind(report, "openapi.path.removed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_operation_removed_is_breaking() -> None:
    before = _shell()
    before["paths"]["/users"] = {"get": _make_op(), "post": _make_op()}
    after = _shell()
    after["paths"]["/users"] = {"get": _make_op()}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.operation.removed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_required_parameter_added_is_breaking() -> None:
    before = _shell()
    before["paths"]["/u"] = {"get": _make_op(parameters=[])}
    after = _shell()
    after["paths"]["/u"] = {
        "get": _make_op(
            parameters=[
                {"in": "query", "name": "q", "required": True, "schema": {"type": "string"}}
            ]
        )
    }
    report = _run(before, after)
    hits = _by_kind(report, "openapi.parameter.added.required")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_parameter_required_increased_is_breaking() -> None:
    before = _shell()
    before["paths"]["/u"] = {
        "get": _make_op(
            parameters=[
                {"in": "query", "name": "q", "required": False, "schema": {"type": "string"}}
            ]
        )
    }
    after = _shell()
    after["paths"]["/u"] = {
        "get": _make_op(
            parameters=[
                {"in": "query", "name": "q", "required": True, "schema": {"type": "string"}}
            ]
        )
    }
    report = _run(before, after)
    hits = _by_kind(report, "openapi.parameter.required.increased")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_parameter_type_changed_is_breaking() -> None:
    before = _shell()
    before["paths"]["/u"] = {
        "get": _make_op(parameters=[{"in": "query", "name": "q", "schema": {"type": "string"}}])
    }
    after = _shell()
    after["paths"]["/u"] = {
        "get": _make_op(parameters=[{"in": "query", "name": "q", "schema": {"type": "integer"}}])
    }
    report = _run(before, after)
    hits = _by_kind(report, "openapi.parameter.type_changed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_required_body_property_added_is_breaking() -> None:
    before = _shell()
    before["paths"]["/u"] = {
        "post": _make_op(
            requestBody={
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "properties": {}, "required": []}
                    }
                }
            }
        )
    }
    after = _shell()
    after["paths"]["/u"] = {
        "post": _make_op(
            requestBody={
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"x": {"type": "string"}},
                            "required": ["x"],
                        }
                    }
                }
            }
        )
    }
    report = _run(before, after)
    hits = _by_kind(report, "openapi.request_body.property.added.required")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_body_property_type_changed_is_breaking() -> None:
    before = _shell()
    before["paths"]["/u"] = {
        "post": _make_op(
            requestBody={
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "properties": {"x": {"type": "string"}}}
                    }
                }
            }
        )
    }
    after = _shell()
    after["paths"]["/u"] = {
        "post": _make_op(
            requestBody={
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "properties": {"x": {"type": "integer"}}}
                    }
                }
            }
        )
    }
    report = _run(before, after)
    hits = _by_kind(report, "openapi.request_body.property.type_changed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_response_removed_is_breaking() -> None:
    before = _shell()
    before["paths"]["/u"] = {
        "get": {"responses": {"200": {"description": "ok"}, "404": {"description": "x"}}}
    }
    after = _shell()
    after["paths"]["/u"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.response.removed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_response_property_removed_is_breaking() -> None:
    before_resp = {
        "200": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                    }
                }
            }
        }
    }
    after_resp = {
        "200": {
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                }
            }
        }
    }
    before = _shell()
    before["paths"]["/u"] = {"get": {"responses": before_resp}}
    after = _shell()
    after["paths"]["/u"] = {"get": {"responses": after_resp}}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.response.property.removed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_response_property_type_changed_is_breaking() -> None:
    def with_type(t: str) -> dict[str, Any]:
        return {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "properties": {"id": {"type": t}}}
                    }
                }
            }
        }

    before = _shell()
    before["paths"]["/u"] = {"get": {"responses": with_type("string")}}
    after = _shell()
    after["paths"]["/u"] = {"get": {"responses": with_type("integer")}}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.response.property.type_changed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_enum_value_removed_is_breaking() -> None:
    before = _shell()
    before["components"]["schemas"]["S"] = {"type": "string", "enum": ["a", "b"]}
    after = _shell()
    after["components"]["schemas"]["S"] = {"type": "string", "enum": ["a"]}
    report = _run(before, after)
    hits = _by_kind(report, "openapi.enum.value.removed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


# ---------- summary + change_id ----------


def test_change_ids_are_stable_across_runs() -> None:
    before = _shell()
    before["paths"]["/users"] = {"get": _make_op()}
    after = _shell()
    r1 = _run(before, after)
    r2 = _run(before, after)
    assert [c.change_id for c in r1.changes] == [c.change_id for c in r2.changes]


def test_summary_counts_match_change_verdicts() -> None:
    before = _shell()
    before["paths"]["/a"] = {"get": _make_op()}  # will be removed
    after = _shell()
    after["paths"]["/b"] = {"get": _make_op()}  # added
    report = _run(before, after)
    assert report.summary.total == len(report.changes)
    assert report.summary.breaking == sum(1 for c in report.changes if c.verdict == "breaking")
    assert report.summary.additive == sum(1 for c in report.changes if c.verdict == "additive")


# Sanity: a no-op diff produces zero changes.
@pytest.mark.parametrize("spec", [_shell()])
def test_noop_diff_yields_no_changes(spec: dict[str, Any]) -> None:
    report = _run(spec, spec)
    assert report.summary.total == 0
    assert report.changes == []
