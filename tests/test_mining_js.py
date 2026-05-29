"""Unit tests for the JavaScript / TypeScript AST visitor."""

from __future__ import annotations

from guardian_core.mining.js_visitor import JsVisitor
from guardian_core.mining.models import Language


def _mine(src: str, lang: Language = "javascript") -> list[tuple[str, str, str, list[str]]]:
    sites = JsVisitor("client.js", src.encode("utf-8"), lang).visit()
    return [(s.client_library, s.method, s.path_template, s.fields) for s in sites]


def test_fetch_template_literal_with_method_init() -> None:
    src = """
const BASE = "https://api.example.com";
function f(id) {
  return fetch(`${BASE}/users/${id}`, { method: 'PATCH', body: JSON.stringify({a: 1, b: 2}) });
}
"""
    sites = _mine(src)
    assert sites == [("fetch", "PATCH", "/users/{id}", ["a", "b"])]


def test_fetch_default_method_is_get() -> None:
    sites = _mine('fetch("/healthz");')
    assert sites == [("fetch", "GET", "/healthz", [])]


def test_axios_get_with_params() -> None:
    src = """
import axios from "axios";
const BASE = "https://api.example.com";
axios.get(`${BASE}/users`, { params: { limit: 10, cursor: "" } });
"""
    sites = _mine(src)
    assert sites == [("axios", "GET", "/users", ["cursor", "limit"])]


def test_axios_post_body_keys() -> None:
    src = """
import axios from "axios";
axios.post("/users", { email: "e", name: "n" });
"""
    sites = _mine(src)
    assert sites == [("axios", "POST", "/users", ["email", "name"])]


def test_axios_request_config_method() -> None:
    src = """
import axios from "axios";
axios.request({ url: "/things", method: "PUT", data: { a: 1 } });
"""
    sites = _mine(src)
    assert sites == [("axios", "PUT", "/things", ["a"])]


def test_axios_renamed_default_import() -> None:
    src = """
import api from "axios";
api.get(`/things/${id}`);
"""
    sites = _mine(src)
    assert sites == [("axios", "GET", "/things/{id}", [])]


def test_typescript_template_string() -> None:
    src = """
import axios from "axios";
const BASE: string = "https://api.example.com";
function f(id: string): Promise<unknown> {
  return axios.get(`${BASE}/widgets/${id}`);
}
"""
    sites = _mine(src, "typescript")
    assert sites == [("axios", "GET", "/widgets/{id}", [])]


def test_unrelated_call_ignored() -> None:
    assert _mine("console.log('hi');") == []
