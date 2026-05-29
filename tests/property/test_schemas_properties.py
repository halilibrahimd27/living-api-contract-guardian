"""Property-based tests for Pydantic schemas.

Invariants tested:
1. ServiceCreate accepts valid non-empty strings for name and owner (1-255 chars)
2. ServiceCreate rejects empty strings, strings > 255 chars, and extra fields
3. ContractUpload accepts valid specs and metadata
4. ContractUpload validates base64 encoding when present
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from guardian_core.schemas import (
    ContractUpload,
    ServiceCreate,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError


# Strategies for generating test data
def _valid_service_name() -> st.SearchStrategy[str]:
    """Generate valid service names (1-255 chars, no special constraints)."""
    return st.text(min_size=1, max_size=255, alphabet=st.characters(blacklist_categories=("Cs",)))


def _valid_owner() -> st.SearchStrategy[str]:
    """Generate valid owner names (1-255 chars)."""
    return st.text(min_size=1, max_size=255, alphabet=st.characters(blacklist_categories=("Cs",)))


def _openapi_spec() -> st.SearchStrategy[dict[str, Any]]:
    """Generate simple valid OpenAPI specs."""
    return st.dictionaries(
        keys=st.text(min_size=1, max_size=20).filter(lambda s: s.isidentifier()),
        values=st.one_of(
            st.none(),
            st.booleans(),
            st.integers(min_value=-100, max_value=100),
            st.text(max_size=50),
        ),
        max_size=10,
    )


def _valid_base64() -> st.SearchStrategy[str]:
    """Generate valid base64 strings."""
    return st.binary(min_size=1, max_size=100).map(lambda b: base64.b64encode(b).decode("utf-8"))


def _invalid_base64() -> st.SearchStrategy[str]:
    """Generate invalid base64 strings."""
    # Remove valid base64 characters and create invalid strings
    return st.text(
        min_size=1,
        max_size=50,
        alphabet="!@#$%^&*()",  # These are not valid base64 chars
    )


class TestServiceCreateValidation:
    """Property tests for ServiceCreate schema."""

    @given(name=_valid_service_name(), owner=_valid_owner())
    def test_service_create_accepts_valid_inputs(self, name: str, owner: str) -> None:
        """ServiceCreate accepts valid name and owner (1-255 chars each)."""
        schema = ServiceCreate(name=name, owner=owner)
        assert schema.name == name
        assert schema.owner == owner

    def test_service_create_rejects_empty_name(self) -> None:
        """ServiceCreate rejects empty name string."""
        with pytest.raises(ValidationError):
            ServiceCreate(name="", owner="valid-owner")

    def test_service_create_rejects_empty_owner(self) -> None:
        """ServiceCreate rejects empty owner string."""
        with pytest.raises(ValidationError):
            ServiceCreate(name="valid-name", owner="")

    @given(name=st.text(min_size=256, max_size=500))
    def test_service_create_rejects_name_too_long(self, name: str) -> None:
        """ServiceCreate rejects name strings longer than 255 chars."""
        with pytest.raises(ValidationError):
            ServiceCreate(name=name, owner="valid-owner")

    @given(owner=st.text(min_size=256, max_size=500))
    def test_service_create_rejects_owner_too_long(self, owner: str) -> None:
        """ServiceCreate rejects owner strings longer than 255 chars."""
        with pytest.raises(ValidationError):
            ServiceCreate(name="valid-name", owner=owner)

    @given(name=_valid_service_name(), owner=_valid_owner())
    def test_service_create_forbids_extra_fields(self, name: str, owner: str) -> None:
        """ServiceCreate with extra fields raises ValidationError."""
        with pytest.raises(ValidationError):
            ServiceCreate(name=name, owner=owner, extra_field="should fail")

    @given(name=_valid_service_name(), owner=_valid_owner())
    def test_service_create_model_serializes_correctly(self, name: str, owner: str) -> None:
        """ServiceCreate serializes to dict with correct keys."""
        schema = ServiceCreate(name=name, owner=owner)
        data = schema.model_dump()
        assert data == {"name": name, "owner": owner}


class TestContractUploadValidation:
    """Property tests for ContractUpload schema."""

    @given(
        name=st.text(min_size=1, max_size=255),
        spec=_openapi_spec(),
        metadata=st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.text(max_size=100),
            max_size=5,
        ),
    )
    def test_contract_upload_accepts_openapi_with_spec(
        self,
        name: str,
        spec: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        """ContractUpload accepts openapi kind with spec dict and metadata."""
        schema = ContractUpload(
            name=name,
            kind="openapi",
            spec=spec,
            spec_metadata=metadata,
        )
        assert schema.name == name
        assert schema.kind == "openapi"
        assert schema.spec == spec
        assert schema.spec_metadata == metadata

    @given(
        name=st.text(min_size=1, max_size=255),
        spec_b64=_valid_base64(),
        metadata=st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.text(max_size=100),
            max_size=5,
        ),
    )
    def test_contract_upload_accepts_proto_with_valid_b64(
        self,
        name: str,
        spec_b64: str,
        metadata: dict[str, Any],
    ) -> None:
        """ContractUpload accepts proto kind with valid base64 spec_b64."""
        schema = ContractUpload(
            name=name,
            kind="proto",
            spec_b64=spec_b64,
            spec_metadata=metadata,
        )
        assert schema.name == name
        assert schema.kind == "proto"
        assert schema.spec_b64 == spec_b64
        assert schema.spec_metadata == metadata

    @given(_invalid_base64())
    def test_contract_upload_rejects_invalid_base64(self, invalid_b64: str) -> None:
        """ContractUpload rejects invalid base64 in spec_b64field."""
        with pytest.raises(ValidationError):
            ContractUpload(
                name="test",
                kind="proto",
                spec_b64=invalid_b64,
            )

    def test_contract_upload_rejects_empty_name(self) -> None:
        """ContractUpload rejects empty name."""
        with pytest.raises(ValidationError):
            ContractUpload(
                name="",
                kind="openapi",
                spec={},
            )

    @given(name=st.text(min_size=256, max_size=500))
    def test_contract_upload_rejects_name_too_long(self, name: str) -> None:
        """ContractUpload rejects name longer than 255 chars."""
        with pytest.raises(ValidationError):
            ContractUpload(
                name=name,
                kind="openapi",
                spec={},
            )

    @given(
        name=st.text(min_size=1, max_size=255),
        spec=_openapi_spec(),
    )
    def test_contract_upload_forbids_extra_fields(
        self,
        name: str,
        spec: dict[str, Any],
    ) -> None:
        """ContractUpload with extra fields raises ValidationError."""
        with pytest.raises(ValidationError):
            ContractUpload(
                name=name,
                kind="openapi",
                spec=spec,
                extra_field="should fail",
            )

    @given(
        name=st.text(min_size=1, max_size=255),
        spec=_openapi_spec(),
    )
    def test_contract_upload_defaults_spec_metadata_to_empty_dict(
        self,
        name: str,
        spec: dict[str, Any],
    ) -> None:
        """ContractUpload defaults spec_metadata to an empty dict."""
        schema = ContractUpload(
            name=name,
            kind="openapi",
            spec=spec,
        )
        assert schema.spec_metadata == {}

    @given(
        name=st.text(min_size=1, max_size=255),
        spec=_openapi_spec(),
    )
    def test_contract_upload_serializes_correctly(
        self,
        name: str,
        spec: dict[str, Any],
    ) -> None:
        """ContractUpload serializes to dict with correct structure."""
        schema = ContractUpload(
            name=name,
            kind="openapi",
            spec=spec,
        )
        data = schema.model_dump()
        assert data["name"] == name
        assert data["kind"] == "openapi"
        assert data["spec"] == spec
        assert data["spec_b64"] is None
        assert data["spec_metadata"] == {}


class TestContractKindLiterals:
    """Property tests for ContractKind literal type."""

    @given(st.just("openapi"))
    def test_contract_kind_accepts_openapi(self, _: str) -> None:
        """ContractUpload accepts kind='openapi'."""
        schema = ContractUpload(
            name="test",
            kind="openapi",
            spec={},
        )
        assert schema.kind == "openapi"

    @given(st.just("proto"))
    def test_contract_kind_accepts_proto(self, _: str) -> None:
        """ContractUpload accepts kind='proto'."""
        schema = ContractUpload(
            name="test",
            kind="proto",
            spec_b64=base64.b64encode(b"test").decode("utf-8"),
        )
        assert schema.kind == "proto"

    @given(st.text().filter(lambda s: s not in ["openapi", "proto"]))
    @settings(suppress_health_check=[HealthCheck.filter_too_much])
    def test_contract_upload_rejects_invalid_kind(self, kind: str) -> None:
        """ContractUpload rejects kind that is not 'openapi' or 'proto'."""
        with pytest.raises(ValidationError):
            ContractUpload(
                name="test",
                kind=kind,  # type: ignore
                spec={},
            )
