"""Integration tests for ``POST /diff``."""

from __future__ import annotations

import base64
from typing import Any

from fastapi.testclient import TestClient
from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    FileDescriptorSet,
)
from guardian_core.db import get_sessionmaker
from guardian_core.models import InferredEndpoint


def _spec_with_path(path: str | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "x", "version": "1"},
        "paths": {},
    }
    if path is not None:
        spec["paths"][path] = {"get": {"responses": {"200": {"description": "ok"}}}}
    return spec


def test_diff_openapi_breaking_returns_classified_changes(client: TestClient) -> None:
    body = {
        "kind": "openapi",
        "before_spec": _spec_with_path("/users"),
        "after_spec": _spec_with_path(None),
    }
    r = client.post("/diff", json=body)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["contract_kind"] == "openapi"
    assert report["summary"]["breaking"] == 1
    assert report["summary"]["total"] == 1
    change = report["changes"][0]
    assert change["kind"] == "openapi.path.removed"
    assert change["verdict"] == "breaking"
    assert change["rule_id"] == "OAS-PATH-REMOVED"
    assert change["change_id"]
    assert isinstance(change["affected_clients"], list)


def test_diff_openapi_additive_only(client: TestClient) -> None:
    body = {
        "kind": "openapi",
        "before_spec": _spec_with_path(None),
        "after_spec": _spec_with_path("/users"),
    }
    r = client.post("/diff", json=body)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["summary"]["additive"] == 1
    assert report["summary"]["breaking"] == 0


def test_diff_openapi_with_custom_rules_yaml(client: TestClient) -> None:
    # The default verdict for a new response code is ``behavioral``; the
    # custom ruleset bumps it to ``breaking``.
    before = _spec_with_path("/u")
    after = _spec_with_path("/u")
    after["paths"]["/u"]["get"]["responses"]["418"] = {"description": "teapot"}
    body = {
        "kind": "openapi",
        "before_spec": before,
        "after_spec": after,
        "rules_yaml": (
            "id: strict\n"
            "rules:\n"
            "  - id: OAS-RESPONSE-ADDED\n"
            "    kind: openapi.response.added\n"
            "    verdict: breaking\n"
            "    rationale: Strict mode.\n"
        ),
    }
    r = client.post("/diff", json=body)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["ruleset_id"] == "strict"
    assert report["summary"]["breaking"] == 1
    assert report["summary"]["behavioral"] == 0


def test_diff_openapi_includes_affected_clients(client: TestClient, migrated_db: str) -> None:
    # Seed an inferred endpoint for /users GET so the affected_clients
    # join finds something.
    sessionmaker = get_sessionmaker()
    with sessionmaker() as s:
        s.add(
            InferredEndpoint(
                repo="acme/users-client",
                commit_sha="deadbeef",
                file="src/api.py",
                line=10,
                language="python",
                client_library="requests",
                method="GET",
                path_template="/users",
                fields={"query": [], "body": []},
                content_hash="cafebabecafebabecafebabecafebabecafebabecafebabecafebabecafebabe",
            )
        )
        s.commit()
    body = {
        "kind": "openapi",
        "before_spec": _spec_with_path("/users"),
        "after_spec": _spec_with_path(None),
    }
    r = client.post("/diff", json=body)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["changes"][0]["affected_clients"] == ["acme/users-client"]


def test_diff_proto_breaking_returns_classified_changes(client: TestClient) -> None:
    def _build(num: int) -> bytes:
        f = FileDescriptorProto()
        f.name = "u.proto"
        f.package = "users"
        msg = DescriptorProto()
        msg.name = "U"
        field = FieldDescriptorProto()
        field.name = "id"
        field.number = num
        field.type = FieldDescriptorProto.TYPE_STRING
        field.label = FieldDescriptorProto.LABEL_OPTIONAL
        msg.field.append(field)
        f.message_type.append(msg)
        fds = FileDescriptorSet()
        fds.file.append(f)
        return fds.SerializeToString()

    body = {
        "kind": "proto",
        "before_b64": base64.b64encode(_build(1)).decode(),
        "after_b64": base64.b64encode(_build(2)).decode(),
    }
    r = client.post("/diff", json=body)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["contract_kind"] == "proto"
    assert report["summary"]["breaking"] == 1
    assert report["changes"][0]["kind"] == "proto.field.number_changed"
    assert report["changes"][0]["rule_id"] == "PROTO-FIELD-NUMBER-CHANGED"


def test_diff_openapi_missing_specs_is_422(client: TestClient) -> None:
    r = client.post("/diff", json={"kind": "openapi"})
    assert r.status_code == 422


def test_diff_proto_invalid_b64_is_422(client: TestClient) -> None:
    r = client.post(
        "/diff",
        json={"kind": "proto", "before_b64": "$$$", "after_b64": "$$$"},
    )
    assert r.status_code == 422


def test_diff_invalid_rules_yaml_is_422(client: TestClient) -> None:
    body = {
        "kind": "openapi",
        "before_spec": _spec_with_path("/u"),
        "after_spec": _spec_with_path("/u"),
        "rules_yaml": "rules:\n  - id: BAD\n    kind: x\n    verdict: nope\n    rationale: r\n",
    }
    r = client.post("/diff", json=body)
    assert r.status_code == 422
