"""Unit tests for the Python AST visitor."""

from __future__ import annotations

from guardian_core.mining.python_visitor import PythonVisitor


def _mine(src: str) -> list[tuple[str, str, str, list[str]]]:
    sites = PythonVisitor("client.py", src.encode("utf-8")).visit()
    return [(s.client_library, s.method, s.path_template, s.fields) for s in sites]


def test_requests_module_call_with_fstring_template() -> None:
    src = """
import requests

BASE = "https://api.example.com"

def get_user(user_id):
    return requests.get(f"{BASE}/users/{user_id}", params={"include": "profile"})
"""
    sites = _mine(src)
    assert ("requests", "GET", "/users/{user_id}", ["include"]) in sites


def test_requests_post_extracts_json_field_names() -> None:
    src = """
import requests
def create(email, name):
    requests.post("https://api.example.com/users", json={"email": email, "name": name})
"""
    sites = _mine(src)
    assert sites == [("requests", "POST", "/users", ["email", "name"])]


def test_requests_request_verb_promoted_to_method() -> None:
    src = """
import requests
def search(q):
    requests.request("PUT", "https://api.example.com/things", params={"q": q})
"""
    sites = _mine(src)
    assert sites == [("requests", "PUT", "/things", ["q"])]


def test_httpx_module_alias() -> None:
    src = """
import httpx as h
def f():
    h.get("https://api.example.com/healthz")
"""
    sites = _mine(src)
    assert sites == [("httpx", "GET", "/healthz", [])]


def test_httpx_async_client_with_block() -> None:
    src = """
import httpx
async def fetch(order_id):
    async with httpx.AsyncClient() as client:
        return await client.get(f"/orders/{order_id}/items")
"""
    sites = _mine(src)
    assert sites == [("httpx", "GET", "/orders/{order_id}/items", [])]


def test_grpc_stub_method_call() -> None:
    src = """
import inventory_pb2 as pb
import inventory_pb2_grpc as pbg

def list_skus(channel, q):
    stub = pbg.InventoryStub(channel)
    return stub.ListSkus(pb.ListSkusRequest(limit=10, cursor=q))
"""
    sites = _mine(src)
    assert sites == [
        ("grpc", "RPC", "/inventory.Inventory/ListSkus", ["cursor", "limit"]),
    ]


def test_non_http_call_is_ignored() -> None:
    src = """
import json
def f():
    json.dumps({"a": 1})
"""
    assert _mine(src) == []


def test_static_id_segment_abstracted() -> None:
    src = """
import requests
def f():
    requests.get("https://api.example.com/users/123/items")
"""
    assert _mine(src) == [("requests", "GET", "/users/{id}/items", [])]
