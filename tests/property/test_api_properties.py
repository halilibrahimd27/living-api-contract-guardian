"""Property-based tests for API endpoints.

Invariants tested:
1. Service creation with valid inputs returns 201 and is idempotent on retrieval
2. Service uniqueness: duplicate names return 409 conflict
3. Contract upload is idempotent: same spec hash returns False for "created"
4. Contract version hash is deterministic and matches canonical form
5. All response fields are present and have correct types
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st


def _valid_service_name() -> st.SearchStrategy[str]:
    """Generate valid service names (1-255 chars)."""
    return st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    )


def _valid_owner() -> st.SearchStrategy[str]:
    """Generate valid owner names (1-255 chars)."""
    return st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    )


def _openapi_spec() -> st.SearchStrategy[dict[str, Any]]:
    """Generate simple valid OpenAPI specs."""
    return st.dictionaries(
        keys=st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
        ),
        values=st.one_of(
            st.none(),
            st.booleans(),
            st.integers(min_value=-100, max_value=100),
            st.text(max_size=30),
        ),
        max_size=5,
    )


class TestServiceCreation:
    """Property tests for service creation endpoint."""

    @given(name=_valid_service_name(), owner=_valid_owner())
    def test_create_service_returns_201_with_valid_inputs(
        self,
        client: TestClient,
        name: str,
        owner: str,
    ) -> None:
        """POST /services with valid inputs returns 201 Created."""
        r = client.post("/services", json={"name": name, "owner": owner})
        assert r.status_code == 201
        body = r.json()
        assert isinstance(body, dict)

    @given(name=_valid_service_name(), owner=_valid_owner())
    def test_create_service_response_has_required_fields(
        self,
        client: TestClient,
        name: str,
        owner: str,
    ) -> None:
        """POST /services response includes id, name, owner, created_at."""
        r = client.post("/services", json={"name": name, "owner": owner})
        assert r.status_code == 201
        body = r.json()
        assert "id" in body
        assert "name" in body
        assert "owner" in body
        assert "created_at" in body
        assert body["name"] == name
        assert body["owner"] == owner

    @given(name=_valid_service_name(), owner=_valid_owner())
    def test_create_service_returns_uuid_id(
        self,
        client: TestClient,
        name: str,
        owner: str,
    ) -> None:
        """Created service has a non-empty id (UUID format)."""
        r = client.post("/services", json={"name": name, "owner": owner})
        assert r.status_code == 201
        body = r.json()
        service_id = body["id"]
        assert isinstance(service_id, str)
        assert len(service_id) > 0
        # UUID format check: 36 chars with dashes
        assert len(service_id) == 36

    @given(name=_valid_service_name(), owner=_valid_owner())
    def test_get_service_returns_same_data_as_create(
        self,
        client: TestClient,
        name: str,
        owner: str,
    ) -> None:
        """GET /services/{id} returns the same data as POST /services."""
        create_r = client.post("/services", json={"name": name, "owner": owner})
        assert create_r.status_code == 201
        created = create_r.json()
        service_id = created["id"]

        get_r = client.get(f"/services/{service_id}")
        assert get_r.status_code == 200
        retrieved = get_r.json()
        assert retrieved == created

    @given(name=_valid_service_name(), owner=_valid_owner())
    def test_duplicate_service_returns_409(
        self,
        client: TestClient,
        name: str,
        owner: str,
    ) -> None:
        """Creating a service with duplicate name returns 409 Conflict."""
        # Create first service
        r1 = client.post("/services", json={"name": name, "owner": owner})
        assert r1.status_code == 201

        # Try to create again with same name
        r2 = client.post("/services", json={"name": name, "owner": "different-owner"})
        assert r2.status_code == 409

    def test_get_nonexistent_service_returns_404(self, client: TestClient) -> None:
        """GET /services/{id} for nonexistent id returns 404."""
        r = client.get("/services/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404


class TestContractUpload:
    """Property tests for contract upload endpoint."""

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
    )
    def test_upload_contract_returns_201_with_valid_inputs(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
    ) -> None:
        """POST /services/{id}/contracts with valid openapi returns 201."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert isinstance(body, dict)

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
    )
    def test_upload_contract_response_has_required_fields(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
    ) -> None:
        """Upload response includes id, kind, version, and created flag."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert "id" in body
        assert "service_id" in body
        assert "name" in body
        assert "kind" in body
        assert "version" in body
        assert "created" in body
        assert body["kind"] == "openapi"
        assert body["created"] is True

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
    )
    def test_upload_contract_version_has_hash(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
    ) -> None:
        """Contract version in response has version_hash (64-char hex)."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
            },
        )
        assert r.status_code == 201
        body = r.json()
        version = body["version"]
        assert "version_hash" in version
        hash_str = version["version_hash"]
        assert isinstance(hash_str, str)
        assert len(hash_str) == 64
        assert all(c in "0123456789abcdef" for c in hash_str)

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
    )
    def test_upload_contract_hash_matches_spec(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
    ) -> None:
        """Version hash matches SHA256 of canonical spec."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
            },
        )
        assert r.status_code == 201
        body = r.json()
        returned_hash = body["version"]["version_hash"]

        # Compute expected hash
        canonical = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected_hash = hashlib.sha256(canonical).hexdigest()
        assert returned_hash == expected_hash

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
    )
    def test_upload_same_contract_twice_is_idempotent(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
    ) -> None:
        """Uploading the same contract twice returns created=False on second upload."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        payload = {
            "name": contract_name,
            "kind": "openapi",
            "spec": spec,
        }

        # First upload
        r1 = client.post(f"/services/{service_id}/contracts", json=payload)
        assert r1.status_code == 201
        assert r1.json()["created"] is True
        hash1 = r1.json()["version"]["version_hash"]

        # Second upload (identical)
        r2 = client.post(f"/services/{service_id}/contracts", json=payload)
        assert r2.status_code == 201
        assert r2.json()["created"] is False
        hash2 = r2.json()["version"]["version_hash"]

        # Hashes must be identical
        assert hash1 == hash2

    def test_upload_contract_to_nonexistent_service_returns_404(self, client: TestClient) -> None:
        """Uploading contract to nonexistent service returns 404."""
        r = client.post(
            "/services/00000000-0000-0000-0000-000000000000/contracts",
            json={
                "name": "test",
                "kind": "openapi",
                "spec": {},
            },
        )
        assert r.status_code == 404

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
    )
    def test_upload_openapi_without_spec_returns_422(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
    ) -> None:
        """Uploading openapi contract without spec returns 422."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
            },
        )
        assert r.status_code == 422

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
    )
    def test_upload_proto_without_spec_b64_returns_422(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
    ) -> None:
        """Uploading proto contract without spec_b64 returns 422."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "proto",
            },
        )
        assert r.status_code == 422


class TestContractVersionMetadata:
    """Property tests for contract metadata handling."""

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
        metadata=st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.text(max_size=50),
            max_size=5,
        ),
    )
    def test_upload_contract_preserves_metadata(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
        metadata: dict[str, str],
    ) -> None:
        """Uploaded contract metadata is preserved in response."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
                "spec_metadata": metadata,
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["version"]["spec_metadata"] == metadata

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
    )
    def test_upload_contract_defaults_empty_metadata(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
    ) -> None:
        """Uploaded contract without metadata defaults to empty dict."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["version"]["spec_metadata"] == {}


class TestContractKindConsistency:
    """Property tests for contract kind consistency within a service."""

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
    )
    def test_upload_contract_with_different_kind_returns_409(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
    ) -> None:
        """Uploading contract with same name but different kind returns 409 Conflict."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        # First, upload an OpenAPI contract
        r1 = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
            },
        )
        assert r1.status_code == 201
        assert r1.json()["kind"] == "openapi"

        # Try to upload the same contract name with proto kind
        r2 = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "proto",
                "spec_b64": base64.b64encode(b"fake proto").decode("utf-8"),
            },
        )
        assert r2.status_code == 409


class TestContractIdempotencyWithDifferentMetadata:
    """Property tests for idempotency behavior with metadata variations."""

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
        metadata1=st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.text(max_size=50),
            max_size=5,
        ),
        metadata2=st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.text(max_size=50),
            max_size=5,
        ),
    )
    def test_upload_same_spec_with_different_metadata_is_idempotent(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
        metadata1: dict[str, str],
        metadata2: dict[str, str],
    ) -> None:
        """Uploading same spec with different metadata is idempotent (first metadata wins)."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        # First upload with metadata1
        r1 = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
                "spec_metadata": metadata1,
            },
        )
        assert r1.status_code == 201
        assert r1.json()["created"] is True
        first_hash = r1.json()["version"]["version_hash"]
        first_metadata = r1.json()["version"]["spec_metadata"]

        # Second upload with same spec but metadata2
        r2 = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
                "spec_metadata": metadata2,
            },
        )
        assert r2.status_code == 201
        assert r2.json()["created"] is False
        second_hash = r2.json()["version"]["version_hash"]
        second_metadata = r2.json()["version"]["spec_metadata"]

        # Hashes must be identical
        assert first_hash == second_hash
        # Metadata should match the first upload (idempotency)
        assert first_metadata == second_metadata


class TestCrossServiceContractNames:
    """Property tests for contract namespace (names are service-local, not global)."""

    @given(
        service1_name=_valid_service_name(),
        service2_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
    )
    def test_same_contract_name_different_services_allowed(
        self,
        client: TestClient,
        service1_name: str,
        service2_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
    ) -> None:
        """Same contract name can be used in different services."""
        # Ensure service names are different
        if service1_name == service2_name:
            return

        # Create two services
        svc1_r = client.post("/services", json={"name": service1_name, "owner": owner})
        svc2_r = client.post("/services", json={"name": service2_name, "owner": owner})
        service1_id = svc1_r.json()["id"]
        service2_id = svc2_r.json()["id"]

        # Upload contract with same name to both services
        r1 = client.post(
            f"/services/{service1_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
            },
        )
        assert r1.status_code == 201

        r2 = client.post(
            f"/services/{service2_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
            },
        )
        assert r2.status_code == 201
        # Both should be new (different services)
        assert r1.json()["id"] != r2.json()["id"]


class TestProtoContractBlobs:
    """Property tests for proto contract blob handling."""

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        raw_bytes=st.binary(min_size=1, max_size=1000),
    )
    def test_proto_contract_stores_raw_bytes_correctly(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        raw_bytes: bytes,
    ) -> None:
        """Proto contract raw_blob is stored verbatim from spec_b64 decoding."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        spec_b64 = base64.b64encode(raw_bytes).decode("utf-8")
        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "proto",
                "spec_b64": spec_b64,
            },
        )
        assert r.status_code == 201
        body = r.json()
        # The hash should match the raw bytes (proto canonicalization is passthrough)
        expected_hash = hashlib.sha256(raw_bytes).hexdigest()
        assert body["version"]["version_hash"] == expected_hash


class TestOpenAPIContractBlobs:
    """Property tests for OpenAPI contract blob handling."""

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
    )
    def test_openapi_contract_hash_matches_canonical_form(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
    ) -> None:
        """OpenAPI contract hash is SHA256 of the canonical (sorted) JSON form."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
            },
        )
        assert r.status_code == 201
        body = r.json()
        returned_hash = body["version"]["version_hash"]

        # Compute what the hash should be using the same canonicalization rules
        canonical = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected_hash = hashlib.sha256(canonical).hexdigest()
        assert returned_hash == expected_hash


class TestResponseFieldFormats:
    """Property tests for response field types and formats."""

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
    )
    def test_service_response_has_valid_uuid_and_timestamp(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
    ) -> None:
        """Service response has valid UUID id and ISO-format created_at timestamp."""
        r = client.post("/services", json={"name": service_name, "owner": owner})
        assert r.status_code == 201
        body = r.json()

        # ID should be a valid UUID (36 chars with dashes)
        service_id = body["id"]
        assert isinstance(service_id, str)
        assert len(service_id) == 36
        assert service_id.count("-") == 4

        # created_at should be a valid ISO format datetime string
        created_at = body["created_at"]
        assert isinstance(created_at, str)
        # Should be parseable as ISO datetime
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))

    @given(
        service_name=_valid_service_name(),
        owner=_valid_owner(),
        contract_name=st.text(min_size=1, max_size=30),
        spec=_openapi_spec(),
    )
    def test_contract_version_response_has_valid_uuids_and_timestamp(
        self,
        client: TestClient,
        service_name: str,
        owner: str,
        contract_name: str,
        spec: dict[str, Any],
    ) -> None:
        """Contract version response has valid UUIDs and ISO-format timestamp."""
        svc_r = client.post("/services", json={"name": service_name, "owner": owner})
        service_id = svc_r.json()["id"]

        r = client.post(
            f"/services/{service_id}/contracts",
            json={
                "name": contract_name,
                "kind": "openapi",
                "spec": spec,
            },
        )
        assert r.status_code == 201
        body = r.json()
        version = body["version"]

        # All ID fields should be valid UUIDs
        for id_field in ["id", "contract_id", "service_id"]:
            uuid_val = version[id_field]
            assert isinstance(uuid_val, str)
            assert len(uuid_val) == 36
            assert uuid_val.count("-") == 4

        # created_at should be valid ISO datetime
        created_at = version["created_at"]
        assert isinstance(created_at, str)
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))

        # version_hash should be 64-char hex
        hash_str = version["version_hash"]
        assert isinstance(hash_str, str)
        assert len(hash_str) == 64
        assert all(c in "0123456789abcdef" for c in hash_str)
