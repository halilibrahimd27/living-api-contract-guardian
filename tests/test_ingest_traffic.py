"""Tests for the traffic-replay contract augmentor.

Covers:
  * ``POST /ingest/traffic`` accepts a HAR upload and returns a merged
    contract id (the curl-verifier criterion).
  * Schema inference correctly types nested JSON fields on fixtures.
  * Field-usage telemetry is idempotent under re-ingest of the same batch
    (counts unchanged, ``last_seen_at`` refreshed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from guardian_core import db as guardian_db
from guardian_core.models import DefactoContract, FieldUsage, IngestBatch, ObservedEndpoint
from guardian_core.traffic.defacto import build_defacto_contract
from guardian_core.traffic.har_parser import parse_har_bytes
from guardian_core.traffic.schema_inference import infer_schema, walk_field_paths
from guardian_core.traffic.url_match import build_route_tree, normalize_observed_path
from sqlalchemy import select
from sqlalchemy.orm import Session

FIXTURES = Path(__file__).parent.parent / "fixtures" / "traffic"


def _openapi_spec_with_users() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {"title": "demo", "version": "1.0.0"},
        "paths": {
            "/v1/users/{user_id}": {"get": {"summary": "fetch user"}},
            "/v1/orders": {"post": {"summary": "create order"}},
        },
    }


def _register_service_with_spec(client: TestClient, spec: dict[str, Any]) -> str:
    svc = client.post("/services", json={"name": "demo", "owner": "team-x"}).json()
    r = client.post(
        f"/services/{svc['id']}/contracts",
        json={"name": "demo-api", "kind": "openapi", "spec": spec},
    )
    assert r.status_code == 201, r.text
    return str(svc["id"])


# --------------------------------------------------------------------------- #
# Acceptance #1: HTTP endpoint returns merged contract id
# --------------------------------------------------------------------------- #


def test_post_ingest_traffic_har_returns_merged_contract_id(client: TestClient) -> None:
    """``POST /ingest/traffic`` with a HAR upload returns a contract id."""
    service_id = _register_service_with_spec(client, _openapi_spec_with_users())
    har_bytes = (FIXTURES / "sample.har").read_bytes()

    r = client.post(
        "/ingest/traffic",
        data={"service_id": service_id, "client_id": "billing-worker"},
        files={"har": ("sample.har", har_bytes, "application/json")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["contract_id"]
    assert body["service_id"] == service_id
    assert body["record_count"] == 3
    assert body["observed_endpoint_count"] >= 2
    assert body["matched_endpoint_count"] >= 1
    assert body["is_duplicate_batch"] is False

    # The returned contract id resolves to a stored defacto contract.
    fetched = client.get(f"/ingest/defacto/{body['contract_id']}")
    assert fetched.status_code == 200, fetched.text
    cj = fetched.json()["contract_json"]
    assert "paths" in cj
    # User template path from the static spec should now carry observed annotations.
    assert "/v1/users/{user_id}" in cj["paths"]
    user_path = cj["paths"]["/v1/users/{user_id}"]
    assert user_path.get("x-source") in {"both", "static"}


def test_post_ingest_traffic_without_payload_is_422(client: TestClient) -> None:
    service_id = _register_service_with_spec(client, _openapi_spec_with_users())
    r = client.post("/ingest/traffic", data={"service_id": service_id})
    assert r.status_code == 422


def test_post_ingest_traffic_unknown_service_is_404(client: TestClient) -> None:
    har_bytes = (FIXTURES / "sample.har").read_bytes()
    r = client.post(
        "/ingest/traffic",
        data={"service_id": "does-not-exist"},
        files={"har": ("sample.har", har_bytes, "application/json")},
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Acceptance #2: schema inference correctly types nested JSON fields
# --------------------------------------------------------------------------- #


def test_schema_inference_types_primitive_fields() -> None:
    samples = [
        {"id": 123, "name": "Ada", "score": 4.5, "verified": True},
        {"id": 456, "name": "Linus", "score": 1.2, "verified": False},
    ]
    schema = infer_schema(samples)
    props = schema["properties"]
    assert props["id"]["type"] == "integer"
    assert props["name"]["type"] == "string"
    assert props["score"]["type"] == "number"
    assert props["verified"]["type"] == "boolean"


def test_schema_inference_types_nested_objects() -> None:
    samples = [
        {"meta": {"verified": True, "score": 4.5}, "id": 1},
        {"meta": {"verified": False, "score": 1.2}, "id": 2},
    ]
    schema = infer_schema(samples)
    meta = schema["properties"]["meta"]
    assert meta["type"] == "object"
    assert meta["properties"]["verified"]["type"] == "boolean"
    assert meta["properties"]["score"]["type"] == "number"


def test_schema_inference_types_arrays_of_objects() -> None:
    samples = [
        {"items": [{"sku": "abc", "qty": 2}]},
        {"items": [{"sku": "xyz", "qty": 1}, {"sku": "qwe", "qty": 3}]},
    ]
    schema = infer_schema(samples)
    items = schema["properties"]["items"]
    assert items["type"] == "array"
    inner = items["items"]
    assert inner["type"] == "object"
    assert inner["properties"]["sku"]["type"] == "string"
    assert inner["properties"]["qty"]["type"] == "integer"


def test_schema_inference_detects_enum_for_low_cardinality_strings() -> None:
    # role is observed as one of {"admin","user","viewer"} many times → enum
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
    schema = infer_schema(samples)
    role = schema["properties"]["role"]
    assert role["type"] == "string"
    assert "enum" in role
    assert set(role["enum"]) == {"admin", "user", "viewer"}


def test_har_parsing_yields_typed_records() -> None:
    raw = (FIXTURES / "sample.har").read_bytes()
    records = parse_har_bytes(raw)
    methods = sorted({r.method for r in records})
    assert methods == ["GET", "POST"]
    post_records = [r for r in records if r.method == "POST"]
    assert post_records
    # request_body is decoded JSON.
    body = post_records[0].request_body
    assert isinstance(body, dict)
    assert body["user_id"] == 123
    assert body["items"][0]["sku"] == "abc"


def test_walk_field_paths_emits_nested_paths() -> None:
    paths = dict(walk_field_paths({"user": {"id": 1, "tags": ["a", "b"]}}))
    assert paths["$"] == "object"
    assert paths["$.user"] == "object"
    assert paths["$.user.id"] == "integer"
    assert paths["$.user.tags"] == "array"
    assert paths["$.user.tags[*]"] == "string"


def test_url_template_match_against_openapi_route_tree() -> None:
    tree = build_route_tree({"paths": {"/v1/users/{id}": {"get": {}}, "/v1/orders": {"post": {}}}})
    tpl, matched = normalize_observed_path("https://api.example.com/v1/users/42", "GET", tree)
    assert tpl == "/v1/users/{id}"
    assert matched == "/v1/users/{id}"
    tpl2, matched2 = normalize_observed_path("https://api.example.com/v1/orders", "POST", tree)
    assert tpl2 == "/v1/orders"
    assert matched2 == "/v1/orders"


def test_url_heuristic_falls_back_when_no_match() -> None:
    tpl, matched = normalize_observed_path("https://api.example.com/things/12345/sub", "GET", None)
    assert tpl == "/things/{id}/sub"
    assert matched is None
    tpl2, matched2 = normalize_observed_path(
        "https://api.example.com/u/123e4567-e89b-12d3-a456-426614174000",
        "GET",
        None,
    )
    assert tpl2 == "/u/{id}"
    assert matched2 is None


def test_defacto_contract_merges_static_and_observed() -> None:
    static = {
        "openapi": "3.0.0",
        "info": {"title": "demo", "version": "1.0.0"},
        "paths": {
            "/v1/users/{id}": {"get": {"summary": "fetch user"}},
        },
    }
    observed = [
        {
            "method": "GET",
            "path_template": "/v1/users/{id}",
            "request_schema": {},
            "response_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
            "sample_count": 7,
            "last_seen_at": "2026-05-29T10:00:00+00:00",
            "matched": True,
        },
        {
            "method": "POST",
            "path_template": "/v1/orders",
            "request_schema": {"type": "object", "properties": {"total": {"type": "number"}}},
            "response_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
            "sample_count": 1,
            "last_seen_at": "2026-05-29T10:00:03+00:00",
            "matched": False,
        },
    ]
    merged = build_defacto_contract(static, observed)
    # static endpoint augmented to "both"
    assert merged["paths"]["/v1/users/{id}"]["x-source"] == "both"
    assert merged["paths"]["/v1/users/{id}"]["get"]["x-sample-count"] == 7
    # observed-only endpoint added with "observed" source
    assert merged["paths"]["/v1/orders"]["x-source"] == "observed"
    assert (
        merged["paths"]["/v1/orders"]["post"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["properties"]["order_id"]["type"]
        == "string"
    )


# --------------------------------------------------------------------------- #
# Acceptance #3: idempotency on re-ingest
# --------------------------------------------------------------------------- #


def _ingest_same_har_twice(
    client: TestClient, service_id: str, har_bytes: bytes
) -> tuple[dict[str, Any], dict[str, Any]]:
    files = {"har": ("sample.har", har_bytes, "application/json")}
    first = client.post(
        "/ingest/traffic",
        data={"service_id": service_id, "client_id": "billing-worker"},
        files=files,
    )
    assert first.status_code == 201, first.text
    # Re-upload exact same payload + client → duplicate batch hash.
    files2 = {"har": ("sample.har", har_bytes, "application/json")}
    second = client.post(
        "/ingest/traffic",
        data={"service_id": service_id, "client_id": "billing-worker"},
        files=files2,
    )
    assert second.status_code == 201, second.text
    return first.json(), second.json()


def _open_db_session() -> Session:
    return guardian_db.get_sessionmaker()()


def test_reingest_same_batch_is_idempotent_for_field_usages(client: TestClient) -> None:
    service_id = _register_service_with_spec(client, _openapi_spec_with_users())
    har_bytes = (FIXTURES / "sample.har").read_bytes()

    first, second = _ingest_same_har_twice(client, service_id, har_bytes)
    assert second["is_duplicate_batch"] is True
    assert first["batch_hash"] == second["batch_hash"]

    db = _open_db_session()
    try:
        # Exactly one IngestBatch row for this service+hash.
        batches = list(db.scalars(select(IngestBatch).where(IngestBatch.service_id == service_id)))
        assert len(batches) == 1
        # Field-usage row count is unchanged after re-ingest.
        field_rows = list(
            db.scalars(
                select(FieldUsage).where(FieldUsage.ingest_batch_hash == first["batch_hash"])
            )
        )
        assert field_rows, "expected field-usage rows from first ingest"
        # Each row's count is the per-batch count (NOT doubled).
        for row in field_rows:
            assert row.count >= 1
        # Total field_usage row count for this batch hash is stable across ingests.
        assert len(field_rows) == first["field_usage_row_count"]
        assert len(field_rows) == second["field_usage_row_count"]
    finally:
        db.close()


def test_reingest_refreshes_last_seen_but_does_not_double_count(client: TestClient) -> None:
    service_id = _register_service_with_spec(client, _openapi_spec_with_users())
    har_bytes = (FIXTURES / "sample.har").read_bytes()

    first_resp = client.post(
        "/ingest/traffic",
        data={"service_id": service_id, "client_id": "billing-worker"},
        files={"har": ("sample.har", har_bytes, "application/json")},
    ).json()

    db = _open_db_session()
    try:
        first_rows = {
            (r.endpoint_id, r.field_path, r.field_role): (r.count, r.last_seen_at)
            for r in db.scalars(
                select(FieldUsage).where(FieldUsage.ingest_batch_hash == first_resp["batch_hash"])
            )
        }
    finally:
        db.close()

    # Second ingest of identical payload.
    second_resp = client.post(
        "/ingest/traffic",
        data={"service_id": service_id, "client_id": "billing-worker"},
        files={"har": ("sample.har", har_bytes, "application/json")},
    ).json()
    assert second_resp["is_duplicate_batch"] is True

    db = _open_db_session()
    try:
        second_rows = {
            (r.endpoint_id, r.field_path, r.field_role): (r.count, r.last_seen_at)
            for r in db.scalars(
                select(FieldUsage).where(FieldUsage.ingest_batch_hash == first_resp["batch_hash"])
            )
        }
    finally:
        db.close()

    assert set(first_rows.keys()) == set(second_rows.keys())
    for key, (c1, t1) in first_rows.items():
        c2, t2 = second_rows[key]
        # count unchanged
        assert c1 == c2, f"count drifted for {key}: {c1} -> {c2}"
        # last_seen_at refreshed (>= original)
        assert t2 >= t1


def test_ingest_persists_observed_endpoint_and_defacto_contract(client: TestClient) -> None:
    service_id = _register_service_with_spec(client, _openapi_spec_with_users())
    har_bytes = (FIXTURES / "sample.har").read_bytes()
    r = client.post(
        "/ingest/traffic",
        data={"service_id": service_id},
        files={"har": ("sample.har", har_bytes, "application/json")},
    )
    assert r.status_code == 201, r.text

    db = _open_db_session()
    try:
        observed = list(
            db.scalars(select(ObservedEndpoint).where(ObservedEndpoint.service_id == service_id))
        )
        assert observed
        # At least one matched our static OpenAPI template /v1/users/{user_id}
        templates = {o.path_template for o in observed}
        assert "/v1/users/{user_id}" in templates
        # /v1/orders is also matched
        assert "/v1/orders" in templates
        defacto_rows = list(
            db.scalars(select(DefactoContract).where(DefactoContract.service_id == service_id))
        )
        assert defacto_rows
        assert defacto_rows[-1].observed_endpoint_count >= 2
    finally:
        db.close()


def test_grpc_log_ingest_also_works(client: TestClient) -> None:
    service_id = _register_service_with_spec(client, _openapi_spec_with_users())
    grpc_bytes = (FIXTURES / "sample.grpc.jsonl").read_bytes()
    r = client.post(
        "/ingest/traffic",
        data={"service_id": service_id, "client_id": "billing-worker"},
        files={"grpc_log": ("sample.grpc.jsonl", grpc_bytes, "application/jsonl")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["record_count"] == 2
    assert body["observed_endpoint_count"] == 1
