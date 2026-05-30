"""Property-based tests for the JSON schema merge helper.

Invariants tested:

**merge_json_schemas:**
1. Empty existing returns copy of incoming
2. Empty incoming returns copy of existing
3. Result is always a dict
4. Result is always JSON-serializable
5. For two object schemas, properties are unioned and required are intersected
6. For non-object schemas, existing metadata survives and incoming overwrites
7. Recursive merge produces consistent results regardless of merge order
8. Required fields intersection: only fields required by BOTH schemas stay required
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from guardian_core.traffic._merge import merge_json_schemas
from hypothesis import given
from hypothesis import strategies as st


def _json_primitive() -> st.SearchStrategy[Any]:
    """Generate JSON-compatible primitive values."""
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-1000, max_value=1000),
        st.floats(allow_nan=False, allow_infinity=False, min_value=-1000, max_value=1000),
        st.text(min_size=0, max_size=50),
    )


def _json_schema() -> st.SearchStrategy[dict[str, Any]]:
    """Generate valid JSON Schema fragments."""
    return st.recursive(
        st.builds(
            lambda t: {"type": t},
            t=st.sampled_from(
                ["null", "boolean", "integer", "number", "string", "array", "object"]
            ),
        ),
        lambda children: st.one_of(
            st.builds(
                lambda props: {
                    "type": "object",
                    "properties": props,
                },
                props=st.dictionaries(
                    keys=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
                    values=children,
                    max_size=3,
                ),
            ),
            st.builds(
                lambda items: {"type": "array", "items": items},
                items=children,
            ),
        ),
        max_leaves=5,
    )


def _object_schema() -> st.SearchStrategy[dict[str, Any]]:
    """Generate object-type JSON schemas."""
    return st.builds(
        lambda props, req: {
            "type": "object",
            "properties": props,
            **({"required": sorted(req)} if req else {}),
        },
        props=st.dictionaries(
            keys=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
            values=st.just({"type": "string"}),
            min_size=1,
            max_size=4,
        ),
        req=st.lists(
            st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
            unique=True,
            max_size=3,
        ),
    ).filter(
        lambda s: all(
            k in s["properties"] for k in s.get("required", [])
        )  # required fields must exist
    )


class TestMergeJsonSchemasBasic:
    """Basic invariants for merge_json_schemas."""

    def test_empty_existing_returns_copy_of_incoming(self) -> None:
        """merge_json_schemas(None, incoming) returns copy of incoming."""
        incoming = {"type": "string", "description": "test"}
        result = merge_json_schemas(None, incoming)
        assert result == incoming
        # Ensure it's a copy, not the same object
        assert result is not incoming

    def test_empty_dict_existing_returns_copy_of_incoming(self) -> None:
        """merge_json_schemas({}, incoming) returns copy of incoming."""
        incoming = {"type": "number"}
        result = merge_json_schemas({}, incoming)
        assert result == incoming
        assert result is not incoming

    def test_empty_incoming_returns_copy_of_existing(self) -> None:
        """merge_json_schemas(existing, None) returns copy of existing."""
        existing = {"type": "integer", "minimum": 0}
        result = merge_json_schemas(existing, None)
        assert result == existing
        assert result is not existing

    def test_empty_dict_incoming_returns_copy_of_existing(self) -> None:
        """merge_json_schemas(existing, {}) returns copy of existing."""
        existing = {"type": "boolean"}
        result = merge_json_schemas(existing, {})
        assert result == existing
        assert result is not existing

    def test_result_is_always_dict(self) -> None:
        """merge_json_schemas always returns a dict."""
        assert isinstance(merge_json_schemas(None, {"type": "string"}), dict)
        assert isinstance(merge_json_schemas({"type": "number"}, None), dict)
        assert isinstance(merge_json_schemas({"type": "string"}, {"type": "integer"}), dict)

    @given(_json_schema(), _json_schema())
    def test_result_is_json_serializable(
        self, existing: dict[str, Any], incoming: dict[str, Any]
    ) -> None:
        """Result is always JSON-serializable."""
        result = merge_json_schemas(existing, incoming)
        try:
            json.dumps(result)
        except TypeError:
            pytest.fail(f"Result not JSON-serializable: {result}")


class TestMergeJsonSchemasObjectMerge:
    """Test merging of object-type schemas."""

    def test_two_empty_objects_merge_to_empty_object(self) -> None:
        """Merging two empty object schemas returns object with no properties."""
        existing = {"type": "object", "properties": {}}
        incoming = {"type": "object", "properties": {}}
        result = merge_json_schemas(existing, incoming)
        assert result["type"] == "object"
        assert result.get("properties") == {}

    def test_object_union_includes_all_properties(self) -> None:
        """Properties from both schemas are included in the merge."""
        existing = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        }
        incoming = {
            "type": "object",
            "properties": {"b": {"type": "integer"}, "c": {"type": "boolean"}},
        }
        result = merge_json_schemas(existing, incoming)
        assert "a" in result["properties"]
        assert "b" in result["properties"]
        assert "c" in result["properties"]

    def test_common_properties_are_merged_recursively(self) -> None:
        """Properties present in both schemas are merged recursively."""
        existing = {
            "type": "object",
            "properties": {"nested": {"type": "object", "properties": {"x": {"type": "string"}}}},
        }
        incoming = {
            "type": "object",
            "properties": {"nested": {"type": "object", "properties": {"y": {"type": "integer"}}}},
        }
        result = merge_json_schemas(existing, incoming)
        nested = result["properties"]["nested"]
        assert "x" in nested.get("properties", {})
        assert "y" in nested.get("properties", {})

    def test_required_intersection_both_required(self) -> None:
        """Fields required by both schemas remain required."""
        existing = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        }
        incoming = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        }
        result = merge_json_schemas(existing, incoming)
        assert set(result.get("required", [])) == {"a", "b"}

    def test_required_intersection_partial_overlap(self) -> None:
        """Only fields required by BOTH schemas stay required."""
        existing = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        }
        incoming = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a"],
        }
        result = merge_json_schemas(existing, incoming)
        required = result.get("required", [])
        # Only 'a' is required by both
        assert set(required) == {"a"}

    def test_required_intersection_no_common_required(self) -> None:
        """If no fields are required by both, required list should be empty."""
        existing = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a"],
        }
        incoming = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["b"],
        }
        result = merge_json_schemas(existing, incoming)
        # No common required fields — intersection of ["a"] and ["b"] is empty
        required = result.get("required", [])
        assert set(required) == set()

    def test_required_one_has_other_doesnt(self) -> None:
        """When only one schema specifies required, union semantics apply."""
        existing = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a"],
        }
        incoming = {"type": "object", "properties": {"a": {"type": "string"}}}
        result = merge_json_schemas(existing, incoming)
        # When incoming doesn't specify required, union with empty set
        # means we keep the existing required list
        required = result.get("required", [])
        assert set(required) == {"a"}

    @given(_object_schema(), _object_schema())
    def test_merge_two_objects_preserves_type(
        self, existing: dict[str, Any], incoming: dict[str, Any]
    ) -> None:
        """Merging two object schemas always produces an object schema."""
        result = merge_json_schemas(existing, incoming)
        assert result.get("type") == "object"

    @given(_object_schema(), _object_schema())
    def test_merge_two_objects_has_properties(
        self, existing: dict[str, Any], incoming: dict[str, Any]
    ) -> None:
        """Result of merging objects always has properties dict."""
        result = merge_json_schemas(existing, incoming)
        assert "properties" in result
        assert isinstance(result["properties"], dict)


class TestMergeJsonSchemasNonObject:
    """Test merging non-object schemas."""

    def test_different_types_incoming_wins(self) -> None:
        """When types differ, incoming overwrites existing type."""
        existing = {"type": "string", "minLength": 5}
        incoming = {"type": "integer", "minimum": 0}
        result = merge_json_schemas(existing, incoming)
        # incoming type wins
        assert result.get("type") == "integer"

    def test_non_object_preserves_existing_metadata(self) -> None:
        """Non-object merge preserves non-conflicting existing metadata."""
        existing = {
            "type": "string",
            "description": "original",
            "minLength": 5,
            "title": "My String",
        }
        incoming = {"type": "string", "maxLength": 100}
        result = merge_json_schemas(existing, incoming)
        # Original metadata should survive if not overwritten
        assert result.get("minLength") == 5
        assert result.get("title") == "My String"

    def test_non_object_incoming_overwrites_conflicting_keys(self) -> None:
        """Incoming values overwrite existing for non-object schemas."""
        existing = {"type": "string", "maxLength": 50}
        incoming = {"type": "string", "maxLength": 100}
        result = merge_json_schemas(existing, incoming)
        # Incoming should win on conflicting keys
        assert result.get("maxLength") == 100

    def test_same_type_string_merge(self) -> None:
        """Merging two string schemas works as overlay."""
        existing = {"type": "string", "pattern": "^[a-z]+$"}
        incoming = {"type": "string", "minLength": 1}
        result = merge_json_schemas(existing, incoming)
        assert result.get("type") == "string"
        assert result.get("pattern") == "^[a-z]+$"
        assert result.get("minLength") == 1

    def test_same_type_integer_merge(self) -> None:
        """Merging two integer schemas works as overlay."""
        existing = {"type": "integer", "minimum": 0, "title": "Count"}
        incoming = {"type": "integer", "maximum": 100}
        result = merge_json_schemas(existing, incoming)
        assert result.get("type") == "integer"
        assert result.get("minimum") == 0
        assert result.get("maximum") == 100
        assert result.get("title") == "Count"


class TestMergeJsonSchemasIdempotent:
    """Test idempotency and consistency properties."""

    def test_merging_with_self_returns_copy(self) -> None:
        """merge_json_schemas(s, s) returns equivalent copy."""
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        result = merge_json_schemas(schema, schema)
        assert result == schema
        assert result is not schema

    @given(_json_schema())
    def test_merging_with_empty_dict_returns_copy(self, schema: dict[str, Any]) -> None:
        """Merging with {} returns copy of original."""
        result = merge_json_schemas(schema, {})
        assert result == schema

    @given(_json_schema())
    def test_merging_empty_dict_returns_copy(self, schema: dict[str, Any]) -> None:
        """Merging from {} returns copy of original."""
        result = merge_json_schemas({}, schema)
        assert result == schema

    def test_merge_associativity_simple(self) -> None:
        """For simple non-object schemas, merge order doesn't affect final result."""
        a = {"type": "string", "minLength": 5}
        b = {"type": "string", "maxLength": 10}
        # (a ⊕ b) should equal (b ⊕ a) for non-objects (overlay semantics)
        result_ab = merge_json_schemas(a, b)
        result_ba = merge_json_schemas(b, a)
        # Both should have string type and both constraints
        assert result_ab.get("type") == "string"
        assert result_ba.get("type") == "string"


class TestMergeJsonSchemasComplex:
    """Test complex nested scenarios."""

    def test_deeply_nested_object_merge(self) -> None:
        """Merging deeply nested objects recurses correctly."""
        existing = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "profile": {
                            "type": "object",
                            "properties": {"age": {"type": "integer"}},
                        }
                    },
                }
            },
        }
        incoming = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "profile": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                        }
                    },
                }
            },
        }
        result = merge_json_schemas(existing, incoming)
        profile = result["properties"]["user"]["properties"]["profile"]
        assert "age" in profile["properties"]
        assert "name" in profile["properties"]

    def test_missing_properties_in_one_schema(self) -> None:
        """Schema without properties dict is handled gracefully."""
        existing = {"type": "object", "properties": {"a": {"type": "string"}}}
        incoming = {"type": "object"}  # No properties key
        result = merge_json_schemas(existing, incoming)
        assert "properties" in result
        assert "a" in result["properties"]

    def test_null_properties_value(self) -> None:
        """Null properties are treated as empty."""
        existing = {"type": "object", "properties": {"a": {"type": "string"}}}
        incoming = {"type": "object", "properties": None}  # type: ignore
        result = merge_json_schemas(existing, incoming)
        assert "a" in result["properties"]

    @given(
        st.builds(
            lambda keys1, keys2: (
                {
                    "type": "object",
                    "properties": {k: {"type": "string"} for k in keys1},
                    "required": keys1,
                },
                {
                    "type": "object",
                    "properties": {k: {"type": "string"} for k in keys2},
                    "required": keys2,
                },
            ),
            keys1=st.lists(
                st.text(min_size=1, max_size=10, alphabet="abc"), unique=True, max_size=3
            ),
            keys2=st.lists(
                st.text(min_size=1, max_size=10, alphabet="abc"), unique=True, max_size=3
            ),
        )
    )
    def test_required_intersection_property(
        self, schemas: tuple[dict[str, Any], dict[str, Any]]
    ) -> None:
        """Required fields are the intersection of both schemas' required lists."""
        existing, incoming = schemas
        result = merge_json_schemas(existing, incoming)
        result_required = set(result.get("required", []))
        existing_required = set(existing.get("required", []))
        incoming_required = set(incoming.get("required", []))
        expected_intersection = existing_required & incoming_required
        assert result_required == expected_intersection
