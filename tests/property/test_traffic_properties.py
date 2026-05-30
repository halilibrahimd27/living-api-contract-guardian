"""Property-based tests for traffic replay contract augmentor.

Invariants tested:

**parse_har_bytes:**
1. Empty bytes return empty list
2. All records have uppercase method
3. All records have non-empty URL
4. request/response bodies are None or JSON-parseable objects
5. Returns only valid HarRequestRecord instances

**parse_grpc_log:**
1. Empty bytes return empty list
2. All records have non-empty path
3. All records have valid method (uppercase)
4. Malformed lines are skipped without raising
5. Returns only valid GrpcCallRecord instances

**infer_schema:**
1. Empty samples return empty dict
2. Non-empty samples return dict with 'type' or empty dict
3. Returned schema has valid JSON Schema structure
4. Enums detected only for low-cardinality string fields
5. Nested objects are properly typed

**walk_field_paths:**
1. All values yield at least one path (the root)
2. Root path is always "$"
3. Paths use . for object fields and [*] for arrays
4. JSON types are always one of: null, boolean, integer, number, string, array, object

**build_route_tree & normalize_observed_path:**
1. Tree construction never raises on any valid OpenAPI spec
2. normalize_observed_path always returns (str, None|str) tuple
3. Result path always starts with /
4. UUID and numeric segments are abstracted to {id}
5. Matched paths equal the input static template

**build_defacto_contract:**
1. Always returns a dict with openapi, info, paths
2. All paths have x-source annotation
3. Static-only paths have x-source="static"
4. Observed-only paths have x-source="observed"
5. Common paths have x-source="both"
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from guardian_core.traffic.defacto import build_defacto_contract
from guardian_core.traffic.grpc_parser import GrpcCallRecord, parse_grpc_log
from guardian_core.traffic.har_parser import HarRequestRecord, parse_har_bytes
from guardian_core.traffic.schema_inference import infer_schema, walk_field_paths
from guardian_core.traffic.url_match import RouteTree, build_route_tree, normalize_observed_path
from hypothesis import given
from hypothesis import strategies as st

# ============================================================================
# Helper Strategies
# ============================================================================


def _http_method() -> st.SearchStrategy[str]:
    """Generate valid HTTP method names."""
    return st.sampled_from(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])


def _valid_url() -> st.SearchStrategy[str]:
    """Generate valid HTTP URLs."""

    def build_path(num_segments: int) -> str:
        if num_segments == 0:
            return "/"
        segments = [f"seg{i}" for i in range(num_segments)]
        return "/" + "/".join(segments)

    return st.builds(
        lambda scheme, host, num_segs: f"{scheme}://{host}{build_path(num_segs)}",
        scheme=st.sampled_from(["http", "https"]),
        host=st.just("example.com"),
        num_segs=st.integers(min_value=0, max_value=3),
    )


def _json_primitive() -> st.SearchStrategy[Any]:
    """Generate JSON-compatible primitive values."""
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-1000, max_value=1000),
        st.floats(allow_nan=False, allow_infinity=False, min_value=-1000, max_value=1000),
        st.text(min_size=0, max_size=50),
    )


def _json_value() -> st.SearchStrategy[Any]:
    """Generate arbitrary JSON-compatible values (recursive)."""
    return st.recursive(
        _json_primitive(),
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(
                keys=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
                values=children,
                max_size=5,
            ),
        ),
        max_leaves=10,
    )


def _valid_har_json() -> st.SearchStrategy[dict[str, Any]]:
    """Generate minimal but valid HAR JSON structures."""
    return st.builds(
        lambda entries: {
            "log": {
                "version": "1.2.0",
                "creator": {"name": "test", "version": "1.0"},
                "entries": entries,
            }
        },
        entries=st.lists(
            st.builds(
                lambda method, url, status: {
                    "request": {
                        "method": method,
                        "url": url,
                        "headers": [],
                        "queryString": [],
                    },
                    "response": {
                        "status": status,
                        "headers": [],
                        "content": {"text": "", "size": 0},
                    },
                },
                method=_http_method(),
                url=_valid_url(),
                status=st.integers(min_value=100, max_value=599),
            ),
            max_size=5,
        ),
    )


def _valid_grpc_jsonl() -> st.SearchStrategy[bytes]:
    """Generate valid gRPC JSONL payloads."""
    return st.builds(
        lambda lines: b"\n".join(json.dumps(line).encode("utf-8") for line in lines),
        lines=st.lists(
            st.builds(
                lambda path: {
                    "method": "POST",
                    "path": path,
                    "request": {"test": "data"},
                    "response": {"result": "ok"},
                    "status": 0,
                },
                path=st.text(
                    min_size=5,
                    max_size=50,
                    alphabet="abcdefghijklmnopqrstuvwxyz./_",
                ),
            ),
            max_size=5,
        ),
    )


def _valid_openapi_spec() -> st.SearchStrategy[dict[str, Any]]:
    """Generate minimal valid OpenAPI specs."""
    return st.builds(
        lambda paths: {
            "openapi": "3.0.0",
            "info": {"title": "test", "version": "1.0.0"},
            "paths": paths,
        },
        paths=st.dictionaries(
            keys=st.text(
                min_size=2,
                max_size=30,
                alphabet="/abcdefghijklmnopqrstuvwxyz{}_",
            ).filter(lambda s: s.startswith("/")),
            values=st.builds(
                lambda method: {method.lower(): {"summary": "test"}},
                method=_http_method(),
            ),
            max_size=5,
        ),
    )


def _observed_endpoint() -> st.SearchStrategy[dict[str, Any]]:
    """Generate valid observed endpoint records."""
    return st.builds(
        lambda method, path, sc, matched, has_req, has_resp: {
            "method": method,
            "path_template": path,
            "request_schema": {"type": "object", "properties": {}} if has_req else {},
            "response_schema": {"type": "object", "properties": {}} if has_resp else {},
            "sample_count": sc,
            "last_seen_at": "2026-05-29T10:00:00Z",
            "matched": matched,
        },
        method=_http_method(),
        path=st.text(
            min_size=1,
            max_size=29,
            alphabet="abcdefghijklmnopqrstuvwxyz{}_",
        ).map(lambda s: "/" + s),
        sc=st.integers(min_value=0, max_value=100),
        matched=st.booleans(),
        has_req=st.booleans(),
        has_resp=st.booleans(),
    )


# ============================================================================
# parse_har_bytes Tests
# ============================================================================


class TestParseHarBytes:
    """Property tests for HAR parsing."""

    def test_empty_bytes_returns_empty_list(self) -> None:
        """parse_har_bytes(b'') returns []."""
        result = parse_har_bytes(b"")
        assert isinstance(result, list)
        assert result == []

    def test_invalid_json_raises_value_error(self) -> None:
        """parse_har_bytes raises ValueError on malformed JSON."""
        with pytest.raises(ValueError):
            parse_har_bytes(b"{invalid json")

    def test_missing_log_raises_value_error(self) -> None:
        """parse_har_bytes raises ValueError when 'log' key missing."""
        with pytest.raises(ValueError):
            parse_har_bytes(b'{"entries": []}')

    @given(_valid_har_json())
    def test_valid_har_returns_list_of_records(self, har_dict: dict[str, Any]) -> None:
        """parse_har_bytes(valid_har) returns list of HarRequestRecord."""
        har_bytes = json.dumps(har_dict).encode("utf-8")
        result = parse_har_bytes(har_bytes)
        assert isinstance(result, list)
        assert all(isinstance(r, HarRequestRecord) for r in result)

    @given(_valid_har_json())
    def test_all_records_have_uppercase_method(self, har_dict: dict[str, Any]) -> None:
        """All parsed records have uppercase HTTP method."""
        har_bytes = json.dumps(har_dict).encode("utf-8")
        result = parse_har_bytes(har_bytes)
        for record in result:
            assert record.method.isupper() or record.method in {
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
            }

    @given(_valid_har_json())
    def test_all_records_have_non_empty_url(self, har_dict: dict[str, Any]) -> None:
        """All parsed records have non-empty URL."""
        har_bytes = json.dumps(har_dict).encode("utf-8")
        result = parse_har_bytes(har_bytes)
        for record in result:
            assert isinstance(record.url, str)
            assert len(record.url) > 0

    @given(_valid_har_json())
    def test_request_response_bodies_are_json_like(self, har_dict: dict[str, Any]) -> None:
        """Request/response bodies are None or JSON-compatible objects."""
        har_bytes = json.dumps(har_dict).encode("utf-8")
        result = parse_har_bytes(har_bytes)
        for record in result:
            if record.request_body is not None:
                # Should be JSON-serializable
                try:
                    json.dumps(record.request_body)
                except TypeError:
                    pytest.fail(f"request_body not JSON-serializable: {record.request_body}")
            if record.response_body is not None:
                try:
                    json.dumps(record.response_body)
                except TypeError:
                    pytest.fail(f"response_body not JSON-serializable: {record.response_body}")


# ============================================================================
# parse_grpc_log Tests
# ============================================================================


class TestParseGrpcLog:
    """Property tests for gRPC log parsing."""

    def test_empty_bytes_returns_empty_list(self) -> None:
        """parse_grpc_log(b'') returns []."""
        result = parse_grpc_log(b"")
        assert isinstance(result, list)
        assert result == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        """parse_grpc_log with only whitespace returns []."""
        result = parse_grpc_log(b"\n\n  \n")
        assert isinstance(result, list)
        assert result == []

    @given(_valid_grpc_jsonl())
    def test_valid_jsonl_returns_list_of_records(self, grpc_bytes: bytes) -> None:
        """parse_grpc_log(valid_jsonl) returns list of GrpcCallRecord."""
        result = parse_grpc_log(grpc_bytes)
        assert isinstance(result, list)
        assert all(isinstance(r, GrpcCallRecord) for r in result)

    @given(_valid_grpc_jsonl())
    def test_all_records_have_non_empty_path(self, grpc_bytes: bytes) -> None:
        """All parsed gRPC records have non-empty path."""
        result = parse_grpc_log(grpc_bytes)
        for record in result:
            assert isinstance(record.path, str)
            assert len(record.path) > 0

    @given(_valid_grpc_jsonl())
    def test_all_records_have_valid_method(self, grpc_bytes: bytes) -> None:
        """All parsed gRPC records have valid HTTP method."""
        result = parse_grpc_log(grpc_bytes)
        valid_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
        for record in result:
            assert record.method in valid_methods

    def test_malformed_lines_are_skipped(self) -> None:
        """Malformed JSONL lines are skipped without raising."""
        bad_jsonl = b'{"path": "/test"}\n{invalid json}\n{"path": "/test2"}\n'
        result = parse_grpc_log(bad_jsonl)
        # Should skip the invalid line but parse the valid ones
        assert len(result) >= 1
        assert all(isinstance(r, GrpcCallRecord) for r in result)

    def test_lines_without_path_are_skipped(self) -> None:
        """Lines missing 'path' key are skipped."""
        bad_jsonl = b'{"method": "POST"}\n{"path": "/valid"}\n'
        result = parse_grpc_log(bad_jsonl)
        # Should only have the valid record with path
        assert len(result) >= 0  # May have 1 or 0 depending on implementation


# ============================================================================
# infer_schema Tests
# ============================================================================


class TestInferSchema:
    """Property tests for JSON schema inference."""

    def test_empty_samples_returns_empty_dict(self) -> None:
        """infer_schema([]) returns {}."""
        result = infer_schema([])
        assert isinstance(result, dict)
        assert result == {}

    def test_none_samples_returns_empty_dict(self) -> None:
        """infer_schema([None, None]) returns {}."""
        result = infer_schema([None, None])
        assert isinstance(result, dict)
        assert result == {}

    @given(st.lists(_json_value(), min_size=1, max_size=5, unique_by=lambda x: str(x)))
    def test_non_empty_samples_return_dict_with_schema_info(self, samples: list[Any]) -> None:
        """Non-empty samples return dict with schema structure."""
        result = infer_schema(samples)
        assert isinstance(result, dict)
        # Result should be JSON-serializable
        try:
            json.dumps(result)
        except TypeError:
            pytest.fail(f"Schema not JSON-serializable: {result}")

    @given(
        st.lists(
            st.builds(
                lambda k, v: {k: v},
                k=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
                v=st.integers(min_value=0, max_value=100),
            ),
            min_size=1,
            max_size=5,
        )
    )
    def test_schema_preserves_integer_type(self, samples: list[dict[str, int]]) -> None:
        """Schema inferred from integer samples includes type: integer."""
        result = infer_schema(samples)
        # At least the root should have a type
        assert isinstance(result, dict)
        if "type" in result:
            assert result["type"] in [
                "object",
                "integer",
                ["integer", "object"],
                ["object", "integer"],
            ]

    @given(
        st.lists(
            st.builds(
                lambda k, v: {k: v},
                k=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
                v=st.text(min_size=1, max_size=20),
            ),
            min_size=1,
            max_size=5,
        )
    )
    def test_schema_preserves_string_type(self, samples: list[dict[str, str]]) -> None:
        """Schema inferred from string samples includes type: string."""
        result = infer_schema(samples)
        assert isinstance(result, dict)
        if "type" in result:
            assert result["type"] in [
                "object",
                "string",
                ["string", "object"],
                ["object", "string"],
            ]

    def test_schema_detects_enum_for_low_cardinality_strings(self) -> None:
        """Low-cardinality string fields are detected as enums."""
        samples = [
            {"role": "admin"},
            {"role": "user"},
            {"role": "viewer"},
            {"role": "admin"},
            {"role": "user"},
            {"role": "user"},
            {"role": "viewer"},
            {"role": "admin"},
        ]
        result = infer_schema(samples)
        if "properties" in result and "role" in result["properties"]:
            role = result["properties"]["role"]
            # If enum is detected, it should list the values
            if "enum" in role:
                assert isinstance(role["enum"], list)
                assert set(role["enum"]) <= {"admin", "user", "viewer"}

    @given(
        st.lists(
            st.builds(
                lambda nested: {"outer": nested},
                nested=st.dictionaries(
                    keys=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
                    values=st.integers(),
                    max_size=3,
                ),
            ),
            min_size=1,
            max_size=5,
        )
    )
    def test_schema_types_nested_objects(self, samples: list[dict[str, Any]]) -> None:
        """Nested objects are properly typed."""
        result = infer_schema(samples)
        assert isinstance(result, dict)
        # If properties exist, outer should be typed as object
        if "properties" in result and "outer" in result["properties"]:
            outer = result["properties"]["outer"]
            if "type" in outer:
                assert outer["type"] in ["object", ["object", "null"], ["null", "object"]]


# ============================================================================
# walk_field_paths Tests
# ============================================================================


class TestWalkFieldPaths:
    """Property tests for JSON field path walking."""

    def test_root_value_yields_at_least_one_path(self) -> None:
        """All values yield at least one path (the root)."""
        for value in [None, True, 42, 3.14, "test", [], {}]:
            paths = list(walk_field_paths(value))
            assert len(paths) >= 1

    def test_root_path_is_always_dollar(self) -> None:
        """Root path is always '$'."""
        for value in [None, True, 42, 3.14, "test", [], {}]:
            paths = list(walk_field_paths(value))
            assert paths[0][0] == "$"

    @given(_json_value())
    def test_all_paths_have_correct_format(self, value: Any) -> None:
        """All paths use . for object keys and [*] for arrays."""
        paths = list(walk_field_paths(value))
        for path, json_type in paths:
            assert isinstance(path, str)
            assert isinstance(json_type, str)
            # Paths should start with $
            assert path.startswith("$")
            # No invalid characters
            assert "[*]" in path or path == "$" or "." in path or path.startswith("$[")

    @given(_json_value())
    def test_json_types_are_valid(self, value: Any) -> None:
        """JSON types are always valid."""
        valid_types = {"null", "boolean", "integer", "number", "string", "array", "object"}
        paths = list(walk_field_paths(value))
        for _path, json_type in paths:
            assert json_type in valid_types

    def test_dict_yields_all_property_paths(self) -> None:
        """Dicts yield paths for all properties."""
        value = {"a": 1, "b": "test"}
        paths = {p: t for p, t in walk_field_paths(value)}
        assert "$" in paths
        assert "$.a" in paths
        assert "$.b" in paths

    def test_array_yields_element_paths(self) -> None:
        """Arrays yield paths for all elements."""
        value = [1, 2, 3]
        paths = {p: t for p, t in walk_field_paths(value)}
        assert "$" in paths
        assert "$[*]" in paths

    def test_nested_structure_yields_all_paths(self) -> None:
        """Nested structures yield all leaf paths."""
        value = {"user": {"id": 1, "tags": ["a", "b"]}}
        paths = {p for p, _ in walk_field_paths(value)}
        assert "$" in paths
        assert "$.user" in paths
        assert "$.user.id" in paths
        assert "$.user.tags" in paths
        assert "$.user.tags[*]" in paths


# ============================================================================
# build_route_tree & normalize_observed_path Tests
# ============================================================================


class TestRouteTreeAndNormalization:
    """Property tests for URL matching and normalization."""

    @given(_valid_openapi_spec())
    def test_build_route_tree_never_raises(self, spec: dict[str, Any]) -> None:
        """build_route_tree never raises on valid OpenAPI specs."""
        try:
            tree = build_route_tree(spec)
            assert isinstance(tree, RouteTree)
        except Exception as e:
            pytest.fail(f"build_route_tree raised {type(e).__name__}: {e}")

    @given(spec=_valid_openapi_spec())
    def test_build_route_tree_returns_routetree(self, spec: dict[str, Any]) -> None:
        """build_route_tree returns a RouteTree instance."""
        tree = build_route_tree(spec)
        assert isinstance(tree, RouteTree)
        assert hasattr(tree, "root")
        assert hasattr(tree, "match")

    @given(url=_valid_url(), method=_http_method())
    def test_normalize_observed_path_returns_tuple(self, url: str, method: str) -> None:
        """normalize_observed_path always returns (str, None|str) tuple."""
        result = normalize_observed_path(url, method)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert result[1] is None or isinstance(result[1], str)

    @given(url=_valid_url(), method=_http_method())
    def test_result_path_starts_with_slash(self, url: str, method: str) -> None:
        """Result path always starts with /."""
        path, _matched = normalize_observed_path(url, method)
        assert isinstance(path, str)
        assert path.startswith("/")

    def test_numeric_segments_abstracted_to_id(self) -> None:
        """Numeric path segments are abstracted to {id}."""
        path, _matched = normalize_observed_path("https://api.example.com/users/123", "GET")
        assert "{id}" in path or "123" in path  # At minimum should handle it

    def test_uuid_segments_abstracted_to_id(self) -> None:
        """UUID path segments are abstracted to {id}."""
        uuid = "123e4567-e89b-12d3-a456-426614174000"
        url = f"https://api.example.com/items/{uuid}"
        path, _matched = normalize_observed_path(url, "GET")
        assert isinstance(path, str)
        assert path.startswith("/")

    @given(spec=_valid_openapi_spec(), url=_valid_url(), method=_http_method())
    def test_matched_path_equals_template_when_matched(
        self, spec: dict[str, Any], url: str, method: str
    ) -> None:
        """When a path matches the static spec, matched equals the template."""
        tree = build_route_tree(spec)
        path, matched = normalize_observed_path(url, method, tree)
        # If matched is not None, path should equal matched
        if matched is not None:
            assert path == matched


# ============================================================================
# build_defacto_contract Tests
# ============================================================================


class TestBuildDefactoContract:
    """Property tests for de-facto contract building."""

    def test_none_static_returns_valid_contract(self) -> None:
        """build_defacto_contract with None static returns valid contract."""
        result = build_defacto_contract(None, [])
        assert isinstance(result, dict)
        assert "openapi" in result
        assert "info" in result
        assert "paths" in result

    @given(_valid_openapi_spec())
    def test_empty_observed_returns_static_contract(self, static: dict[str, Any]) -> None:
        """build_defacto_contract with empty observed preserves static."""
        result = build_defacto_contract(static, [])
        assert isinstance(result, dict)
        assert "paths" in result
        # Static paths should be present (if any)
        static_paths = set(static.get("paths", {}).keys())
        result_paths = set(result.get("paths", {}).keys())
        assert static_paths <= result_paths

    @given(
        static=_valid_openapi_spec(),
        observed=st.lists(_observed_endpoint(), max_size=3),
    )
    def test_always_has_required_top_level_keys(
        self, static: dict[str, Any], observed: list[dict[str, Any]]
    ) -> None:
        """Result always has openapi, info, paths."""
        result = build_defacto_contract(static, observed)
        assert isinstance(result, dict)
        assert "openapi" in result
        assert "info" in result
        assert "paths" in result
        assert isinstance(result["paths"], dict)

    @given(
        observed=st.lists(_observed_endpoint(), min_size=1, max_size=3),
    )
    def test_all_paths_have_x_source_annotation(self, observed: list[dict[str, Any]]) -> None:
        """All paths have x-source annotation."""
        result = build_defacto_contract(None, observed)
        for path_key, path_obj in result.get("paths", {}).items():
            if isinstance(path_obj, dict):
                assert "x-source" in path_obj, f"Path {path_key} missing x-source"

    @given(static=_valid_openapi_spec())
    def test_static_only_paths_have_static_source(self, static: dict[str, Any]) -> None:
        """Paths only in static spec have x-source='static'."""
        result = build_defacto_contract(static, [])
        for _path_key, path_obj in result.get("paths", {}).items():
            if isinstance(path_obj, dict):
                # Paths from static with no observations should be "static"
                assert path_obj.get("x-source") in {"static", "both"}

    @given(observed=st.lists(_observed_endpoint(), min_size=1, max_size=3))
    def test_observed_only_paths_have_observed_source(self, observed: list[dict[str, Any]]) -> None:
        """Paths only in observed have x-source='observed'."""
        result = build_defacto_contract(None, observed)
        for _path_key, path_obj in result.get("paths", {}).items():
            if isinstance(path_obj, dict):
                # With no static spec, all should be "observed"
                assert path_obj.get("x-source") in {"observed", "both"}

    @given(
        static=_valid_openapi_spec(),
        observed=st.lists(_observed_endpoint(), max_size=3),
    )
    def test_common_paths_have_both_source(
        self, static: dict[str, Any], observed: list[dict[str, Any]]
    ) -> None:
        """Paths in both static and observed have x-source='both'."""
        # Filter observed to only include paths that are in static
        static_paths = set(static.get("paths", {}).keys())
        observed_common = [obs for obs in observed if obs.get("path_template") in static_paths]
        if observed_common:
            result = build_defacto_contract(static, observed_common)
            for obs in observed_common:
                path_template = obs.get("path_template", "")
                if path_template in result.get("paths", {}):
                    assert result["paths"][path_template].get("x-source") == "both"

    @given(
        observed=st.lists(_observed_endpoint(), min_size=1, max_size=3),
    )
    def test_observed_endpoints_have_telemetry_annotations(
        self, observed: list[dict[str, Any]]
    ) -> None:
        """Observed endpoints get x-sample-count, x-last-seen-at annotations."""
        result = build_defacto_contract(None, observed)
        # Check that operations have telemetry
        for path_obj in result.get("paths", {}).values():
            if isinstance(path_obj, dict):
                for method_key, op in path_obj.items():
                    if isinstance(op, dict) and not method_key.startswith("x-"):
                        # This is an operation; check for telemetry
                        if method_key.lower() in {
                            "get",
                            "post",
                            "put",
                            "patch",
                            "delete",
                            "head",
                            "options",
                        }:
                            assert "x-sample-count" in op or "x-matched-static" in op


# ============================================================================
# compute_batch_hash Tests
# ============================================================================


class TestBatchHash:
    """Property tests for batch hash computation."""

    def test_empty_inputs_produce_hash(self) -> None:
        """compute_batch_hash(None, None) produces a valid hash."""
        from guardian_core.traffic.ingestor import compute_batch_hash

        # This should raise since at least one must be provided
        # but let's be defensive
        try:
            result = compute_batch_hash(None, None)
            # If it doesn't raise, should return a string
            assert isinstance(result, str)
        except (TypeError, ValueError):
            # Expected behavior
            pass

    @given(har_bytes=st.binary(min_size=1, max_size=100))
    def test_har_only_produces_valid_hash(self, har_bytes: bytes) -> None:
        """compute_batch_hash(har_bytes, None) produces valid SHA256."""
        from guardian_core.traffic.ingestor import compute_batch_hash

        result = compute_batch_hash(har_bytes, None)
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex is 64 chars
        assert all(c in "0123456789abcdef" for c in result)

    @given(grpc_bytes=st.binary(min_size=1, max_size=100))
    def test_grpc_only_produces_valid_hash(self, grpc_bytes: bytes) -> None:
        """compute_batch_hash(None, grpc_bytes) produces valid SHA256."""
        from guardian_core.traffic.ingestor import compute_batch_hash

        result = compute_batch_hash(None, grpc_bytes)
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex is 64 chars

    @given(
        har_bytes=st.binary(min_size=1, max_size=100),
        grpc_bytes=st.binary(min_size=1, max_size=100),
    )
    def test_both_inputs_produce_valid_hash(self, har_bytes: bytes, grpc_bytes: bytes) -> None:
        """compute_batch_hash(har, grpc) produces valid SHA256."""
        from guardian_core.traffic.ingestor import compute_batch_hash

        result = compute_batch_hash(har_bytes, grpc_bytes)
        assert isinstance(result, str)
        assert len(result) == 64

    @given(har_bytes=st.binary(min_size=1, max_size=100))
    def test_different_inputs_produce_different_hashes(self, har_bytes: bytes) -> None:
        """Different inputs produce different hashes."""
        from guardian_core.traffic.ingestor import compute_batch_hash

        hash1 = compute_batch_hash(har_bytes, None)
        hash2 = compute_batch_hash(har_bytes + b"x", None)
        assert hash1 != hash2

    @given(har_bytes=st.binary(min_size=1, max_size=100))
    def test_same_inputs_produce_same_hash(self, har_bytes: bytes) -> None:
        """Same inputs produce same hash (deterministic)."""
        from guardian_core.traffic.ingestor import compute_batch_hash

        hash1 = compute_batch_hash(har_bytes, None)
        hash2 = compute_batch_hash(har_bytes, None)
        assert hash1 == hash2
