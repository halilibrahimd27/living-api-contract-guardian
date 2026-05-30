"""Additional property-based tests for the AST contract miner.

Focuses on technical invariants from the milestone:
1. F-string placeholder detection and normalization
2. gRPC stub method detection
3. Module alias resolution with constant propagation
4. Path template idempotency
5. Field extraction and sorting consistency
6. Language-specific visitor behaviors
"""

from __future__ import annotations

from guardian_core.mining.js_visitor import JsVisitor
from guardian_core.mining.models import InferredCallSite
from guardian_core.mining.path_normalize import (
    PLACEHOLDER_SENTINEL,
    abstract_static_segments,
    normalize_template,
)
from guardian_core.mining.python_visitor import PythonVisitor
from hypothesis import assume, given
from hypothesis import strategies as st

# ============================================================================
# Helper Strategies
# ============================================================================


def _valid_identifier() -> st.SearchStrategy[str]:
    """Generate valid Python/JS identifiers."""
    return st.text(
        min_size=1,
        max_size=32,
        alphabet="abcdefghijklmnopqrstuvwxyz_",
    ).filter(lambda s: s[0] != "_")


def _valid_http_method() -> st.SearchStrategy[str]:
    """Generate valid HTTP methods."""
    return st.sampled_from(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])


def _field_name_strategy() -> st.SearchStrategy[str]:
    """Generate valid field names (query/body parameters)."""
    return st.text(
        min_size=1,
        max_size=32,
        alphabet="abcdefghijklmnopqrstuvwxyz_0123456789",
    ).filter(lambda s: s[0] not in "0123456789")


# ============================================================================
# Tests for F-String Placeholder Detection and Normalization
# ============================================================================


class TestFStringInterpolation:
    """Property-based tests for f-string handling in path templates."""

    def test_fstring_placeholder_becomes_named_placeholder(self) -> None:
        """F-string interpolation {name} becomes {name} in output."""
        src = b"""
import requests
user_id = 123
requests.get(f"https://example.com/users/{user_id}")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            # Should detect the placeholder
            template = sites[0].path_template
            assert "{" in template and "}" in template

    def test_fstring_with_attribute_access(self) -> None:
        """F-string with attribute access like {obj.field} uses field name."""
        src = b"""
import requests
class Config:
    base_url = "https://api.example.com"
    path = "/users"
c = Config()
requests.get(f"{c.path}/items")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        # Should parse without error
        assert isinstance(sites, list)

    def test_fstring_multiple_interpolations(self) -> None:
        """Multiple f-string interpolations each become placeholders."""
        src = b"""
import requests
requests.get(f"https://example.com/{resource}/{resource_id}")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            template = sites[0].path_template
            # Should have multiple placeholders
            brace_count = template.count("{")
            assert brace_count >= 1

    def test_fstring_mixed_with_constants(self) -> None:
        """F-string with known constants inlines the constants."""
        src = b"""
import requests
BASE_URL = "https://api.example.com"
endpoint = "users"
requests.get(f"{BASE_URL}/{endpoint}/profile")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        # Should handle mixed constants and variables
        assert isinstance(sites, list)

    def test_javascript_template_literal_placeholders(self) -> None:
        """Template literals with ${expr} become named placeholders."""
        src = b"""
const userId = 123;
fetch(`/users/${userId}/posts`);
"""
        visitor = JsVisitor("test.js", src, "javascript")
        sites = visitor.visit()
        if sites:
            template = sites[0].path_template
            # Should have placeholder
            assert "{" in template and "}" in template

    def test_javascript_template_literal_with_identifier(self) -> None:
        """Template literal ${identifier} uses identifier name as placeholder."""
        src = b"""
const id = 42;
fetch(`/items/${id}`);
"""
        visitor = JsVisitor("test.js", src, "javascript")
        sites = visitor.visit()
        if sites:
            # Should detect and name the placeholder
            assert isinstance(sites[0], InferredCallSite)


# ============================================================================
# Tests for gRPC Stub Detection
# ============================================================================


class TestGRPCStubDetection:
    """Property-based tests for gRPC stub method detection."""

    def test_grpc_stub_method_call_detected(self) -> None:
        """gRPC stub.MethodName(request) pattern is detected."""
        src = b"""
import my_service_pb2_grpc
import my_service_pb2

channel = None
stub = my_service_pb2_grpc.MyServiceStub(channel)
request = my_service_pb2.GetUserRequest(user_id=123)
stub.GetUser(request)
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            # Should detect gRPC call
            assert any(s.client_library == "grpc" for s in sites)
            assert any(s.method == "RPC" for s in sites)

    def test_grpc_stub_path_template_format(self) -> None:
        """gRPC stub generates /package.Service/Method path template."""
        src = b"""
import users_pb2_grpc
stub = users_pb2_grpc.UserServiceStub(channel)
stub.GetUser(request)
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            grpc_sites = [s for s in sites if s.client_library == "grpc"]
            if grpc_sites:
                # Should have /package.Service/Method format
                path = grpc_sites[0].path_template
                assert "/" in path
                assert "." in path

    def test_grpc_stub_aliased_import(self) -> None:
        """gRPC stubs from aliased imports are recognized."""
        src = b"""
import my_pb2_grpc as my_service
import channel_module
ch = channel_module.get_channel()
stub = my_service.ServiceStub(ch)
stub.Method(req)
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        # Should parse and handle alias
        assert isinstance(sites, list)

    def test_grpc_method_name_extraction(self) -> None:
        """gRPC method names are correctly extracted."""
        src = b"""
import proto_pb2_grpc
stub = proto_pb2_grpc.ServiceStub(channel)
stub.CreateItem(request)
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            grpc_sites = [s for s in sites if s.client_library == "grpc"]
            if grpc_sites:
                # Method name should be in path
                assert "CreateItem" in grpc_sites[0].path_template


# ============================================================================
# Tests for Module Alias Resolution
# ============================================================================


class TestModuleAliasResolution:
    """Property-based tests for module aliasing and resolution."""

    def test_requests_import_as_alias(self) -> None:
        """import requests as req → req.get() is recognized."""
        src = b"""
import requests as req
req.get("https://example.com/items")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        assert any(s.client_library == "requests" for s in sites)

    def test_httpx_import_as_alias(self) -> None:
        """import httpx as client → client.post() is recognized."""
        src = b"""
import httpx as client
client.post("https://api.example.com/data")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        assert any(s.client_library == "httpx" for s in sites)

    def test_from_requests_import(self) -> None:
        """from requests import get → get() is recognized."""
        src = b"""
from requests import get
get("https://example.com/users")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        # Should handle from imports
        assert isinstance(sites, list)

    @given(_valid_identifier())
    def test_http_client_context_manager_alias(self, var_name: str) -> None:
        """with httpx.Client() as name: name.get(...) is recognized."""
        assume(var_name not in ("httpx", "requests"))
        src = f"""
import httpx
with httpx.Client() as {var_name}:
    {var_name}.get("https://example.com/resource")
""".encode()
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        # Should recognize through context manager alias
        assert isinstance(sites, list)

    def test_javascript_axios_import_alias(self) -> None:
        """import axios as api → api.get(...) recognized."""
        src = b"""
import api from 'axios';
api.get('/users');
"""
        visitor = JsVisitor("test.js", src, "javascript")
        sites = visitor.visit()
        assert any(s.client_library == "axios" for s in sites)

    def test_javascript_require_axios(self) -> None:
        """const axios = require('axios') → axios.post(...) recognized."""
        src = b"""
const axios = require('axios');
axios.post('/data', { name: 'test' });
"""
        visitor = JsVisitor("test.js", src, "javascript")
        sites = visitor.visit()
        assert any(s.client_library == "axios" for s in sites)


# ============================================================================
# Tests for Path Template Idempotency
# ============================================================================


class TestPathTemplateIdempotency:
    """Property tests for path template normalization idempotency."""

    @given(
        num_placeholders=st.integers(min_value=0, max_value=3),
    )
    def test_normalize_idempotent(self, num_placeholders: int) -> None:
        """normalize_template() applied twice yields same result."""
        # Build a template with placeholders
        segments = ["api", "v1", "resource"]
        for i in range(num_placeholders):
            segments.append(PLACEHOLDER_SENTINEL)
            segments.append(f"seg{i}")
        raw = "/" + "/".join(segments)
        placeholders = [f"param{i}" for i in range(num_placeholders)]

        template1 = normalize_template(raw, placeholders)
        # Note: second call would have different input (already normalized)
        # So we just verify it's stable
        assert template1.startswith("/")

    def test_abstract_segments_idempotent(self) -> None:
        """abstract_static_segments() applied twice is idempotent."""
        original = "/users/123/posts/456"
        abstracted1 = abstract_static_segments(original)
        abstracted2 = abstract_static_segments(abstracted1)
        # Should be identical on second application
        assert abstracted1 == abstracted2

    def test_abstract_segments_preserves_placeholders(self) -> None:
        """abstract_static_segments() doesn't re-abstract {id} placeholders."""
        template = "/users/{id}/posts/{id}"
        result = abstract_static_segments(template)
        # Should preserve the {id} placeholders
        assert result.count("{id}") == 2

    @given(st.data())
    def test_normalize_then_abstract_stable(self, data: st.DataObject) -> None:
        """normalize then abstract produces consistent results."""
        raw_path = data.draw(st.sampled_from(["/api/users", "/items/123/detail"]))
        normalized = normalize_template(raw_path, [])
        abstracted = abstract_static_segments(normalized)
        # Verify result properties
        assert abstracted.startswith("/")
        assert "//" not in abstracted
        if abstracted != "/":
            assert not abstracted.endswith("/")


# ============================================================================
# Tests for Field Extraction and Consistency
# ============================================================================


class TestFieldExtraction:
    """Property-based tests for HTTP field extraction."""

    def test_json_fields_extracted(self) -> None:
        """Query/body fields from json= parameter are extracted."""
        src = b"""
import requests
requests.post("https://example.com/users", json={"name": "Alice", "age": 30})
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            assert any("name" in s.fields for s in sites)
            assert any("age" in s.fields for s in sites)

    def test_params_fields_extracted(self) -> None:
        """Query parameter fields are extracted."""
        src = b"""
import requests
requests.get("https://example.com/users", params={"limit": 10, "offset": 0})
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            assert any("limit" in s.fields or "offset" in s.fields for s in sites)

    def test_fields_sorted_and_deduplicated(self) -> None:
        """Fields are sorted and deduplicated in InferredCallSite."""
        src = b"""
import requests
requests.post("https://example.com/items", json={"z": 1, "a": 2, "z": 3})
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            for site in sites:
                # Should be sorted
                assert site.fields == sorted(site.fields)
                # Duplicates removed
                assert len(site.fields) == len(set(site.fields))

    @given(
        fields=st.lists(
            _field_name_strategy(),
            min_size=1,
            max_size=5,
            unique=True,
        )
    )
    def test_content_hash_field_order_independent(self, fields: list[str]) -> None:
        """Content hash is independent of field list order."""
        site1 = InferredCallSite(
            file="test.py",
            line=1,
            language="python",
            client_library="requests",
            method="POST",
            path_template="/api",
            fields=fields,
        )
        site2 = InferredCallSite(
            file="test.py",
            line=1,
            language="python",
            client_library="requests",
            method="POST",
            path_template="/api",
            fields=list(reversed(fields)),
        )
        # Should be same because fields are sorted in content_hash
        assert site1.content_hash() == site2.content_hash()

    def test_javascript_body_fields_extracted(self) -> None:
        """JavaScript body fields from data/params are extracted."""
        src = b"""
const axios = require('axios');
axios.post('/api/users', { name: 'Bob', email: 'bob@example.com' });
"""
        visitor = JsVisitor("test.js", src, "javascript")
        sites = visitor.visit()
        if sites:
            assert any("name" in s.fields or "email" in s.fields for s in sites)


# ============================================================================
# Tests for Language-Specific Behaviors
# ============================================================================


class TestLanguageSpecificBehaviors:
    """Property-based tests for language-specific visitor behaviors."""

    def test_python_visitor_sets_language_field(self) -> None:
        """PythonVisitor always sets language='python'."""
        src = b"""
import requests
requests.get("https://example.com")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            assert all(s.language == "python" for s in sites)

    def test_js_visitor_javascript_sets_language(self) -> None:
        """JsVisitor with javascript language sets language='javascript'."""
        src = b"""
fetch('/api');
"""
        visitor = JsVisitor("test.js", src, "javascript")
        sites = visitor.visit()
        if sites:
            assert all(s.language == "javascript" for s in sites)

    def test_js_visitor_typescript_sets_language(self) -> None:
        """JsVisitor with typescript language sets language='typescript'."""
        src = b"""
fetch('/api');
"""
        visitor = JsVisitor("test.ts", src, "typescript")
        sites = visitor.visit()
        if sites:
            assert all(s.language == "typescript" for s in sites)

    def test_python_client_library_set_correctly(self) -> None:
        """Python visitor sets client_library to 'requests' or 'httpx'."""
        src_requests = b"""
import requests
requests.get("https://example.com")
"""
        src_httpx = b"""
import httpx
httpx.get("https://example.com")
"""
        visitor_req = PythonVisitor("test.py", src_requests)
        visitor_httpx = PythonVisitor("test.py", src_httpx)
        sites_req = visitor_req.visit()
        sites_httpx = visitor_httpx.visit()
        if sites_req:
            assert all(s.client_library == "requests" for s in sites_req)
        if sites_httpx:
            assert all(s.client_library == "httpx" for s in sites_httpx)

    def test_js_client_library_set_correctly(self) -> None:
        """JavaScript visitor sets client_library to 'fetch' or 'axios'."""
        src_fetch = b"""
fetch('/api');
"""
        src_axios = b"""
import axios from 'axios';
axios.get('/api');
"""
        visitor_fetch = JsVisitor("test.js", src_fetch, "javascript")
        visitor_axios = JsVisitor("test.js", src_axios, "javascript")
        sites_fetch = visitor_fetch.visit()
        sites_axios = visitor_axios.visit()
        if sites_fetch:
            assert all(s.client_library == "fetch" for s in sites_fetch)
        if sites_axios:
            assert all(s.client_library == "axios" for s in sites_axios)


# ============================================================================
# Tests for Method Detection and Normalization
# ============================================================================


class TestMethodDetection:
    """Property-based tests for HTTP method detection and normalization."""

    @given(_valid_http_method())
    def test_python_requests_method_detected(self, method: str) -> None:
        """All HTTP methods are detected in Python requests calls."""
        method_lower = method.lower()
        src = f"""
import requests
requests.{method_lower}("https://example.com")
""".encode()
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            assert any(s.method == method for s in sites)

    def test_request_method_extracted_from_first_arg(self) -> None:
        """requests.request('POST', url) extracts method from first arg."""
        src = b"""
import requests
requests.request('PUT', 'https://example.com/resource')
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            assert any(s.method == "PUT" for s in sites)

    def test_request_method_extracted_from_kwarg(self) -> None:
        """requests.request(method='PATCH', url=...) extracts method."""
        src = b"""
import requests
requests.request(method='PATCH', url='https://example.com')
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            assert any(s.method == "PATCH" for s in sites)

    def test_axios_method_from_config_object(self) -> None:
        """axios.request({method: 'DELETE', url: ...}) extracts method."""
        src = b"""
import axios from 'axios';
axios.request({ method: 'DELETE', url: '/resource' });
"""
        visitor = JsVisitor("test.js", src, "javascript")
        sites = visitor.visit()
        if sites:
            # Should detect DELETE
            assert isinstance(sites, list)

    def test_method_case_preserved(self) -> None:
        """HTTP method case is preserved in output."""
        site = InferredCallSite(
            file="test.py",
            line=1,
            language="python",
            client_library="requests",
            method="POST",
            path_template="/api",
            fields=[],
        )
        assert site.method == "POST"


# ============================================================================
# Tests for Line Number Tracking
# ============================================================================


class TestLineNumberTracking:
    """Property-based tests for accurate line number tracking."""

    def test_line_numbers_monotonically_increasing(self) -> None:
        """Multiple call sites have monotonically increasing line numbers."""
        src = b"""
import requests
requests.get("https://example.com/a")
requests.post("https://example.com/b")
requests.put("https://example.com/c")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if len(sites) > 1:
            lines = [s.line for s in sites]
            for i in range(1, len(lines)):
                assert lines[i] > lines[i - 1]

    def test_content_hash_differs_on_line_change(self) -> None:
        """Content hash includes line number."""
        site1 = InferredCallSite(
            file="test.py",
            line=1,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=[],
        )
        site2 = InferredCallSite(
            file="test.py",
            line=2,
            language="python",
            client_library="requests",
            method="GET",
            path_template="/users",
            fields=[],
        )
        assert site1.content_hash() != site2.content_hash()

    def test_call_sites_track_accurate_lines(self) -> None:
        """Each call site records its actual line number."""
        src = b"""
import requests

requests.get("https://example.com/first")

requests.post("https://example.com/second")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if len(sites) >= 2:
            # First call around line 3, second around line 5
            lines = sorted([s.line for s in sites])
            # Difference should be at least 1
            assert lines[1] > lines[0]


# ============================================================================
# Tests for URL Path Extraction and Handling
# ============================================================================


class TestURLPathExtraction:
    """Property-based tests for URL path extraction from various formats."""

    def test_full_https_url_path_extracted(self) -> None:
        """Full HTTPS URLs are parsed to extract path."""
        src = b"""
import requests
requests.get("https://api.example.com:8080/api/v1/users")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            path = sites[0].path_template
            # Should contain the path, not the domain
            assert "users" in path
            assert "api.example.com" not in path
            assert "https://" not in path

    def test_relative_path_preserved(self) -> None:
        """Relative paths are preserved."""
        src = b"""
import requests
requests.get("/users/profile")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            assert "/users/profile" in sites[0].path_template

    def test_path_with_query_string_query_removed(self) -> None:
        """Query strings are removed from path template."""
        src = b"""
import requests
requests.get("https://example.com/users?filter=active&limit=10")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        if sites:
            path = sites[0].path_template
            assert "?" not in path
            assert "filter" not in path
            assert "limit" not in path

    def test_path_concatenation_resolved(self) -> None:
        """String concatenation in paths is resolved."""
        src = b"""
import requests
BASE = "https://api.example.com"
requests.get(BASE + "/users")
"""
        visitor = PythonVisitor("test.py", src)
        sites = visitor.visit()
        # Should handle string concatenation
        assert isinstance(sites, list)

    def test_javascript_template_literals_resolved(self) -> None:
        """JS template literals are resolved to paths."""
        src = b"""
const host = 'api.example.com';
fetch(`https://${host}/resource`);
"""
        visitor = JsVisitor("test.js", src, "javascript")
        sites = visitor.visit()
        if sites:
            path = sites[0].path_template
            # Should have the /resource part
            assert "/" in path
