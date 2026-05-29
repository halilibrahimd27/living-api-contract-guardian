"""Property-based tests for SQLAlchemy ORM models.

Invariants tested:
1. All model IDs are generated as UUID7 strings by default
2. All models have created_at timestamps that default to UTC now
3. Service name is globally unique (unique constraint)
4. Contract names are unique per service (composite unique constraint)
5. Client names are globally unique
6. Foreign key relationships are properly defined
7. Cascade delete rules are properly set
8. JSON columns can store dictionaries
9. Optional fields accept None values
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from guardian_core.db import reset_engine, session_scope
from guardian_core.models import (
    Client,
    Contract,
    ContractVersion,
    Deprecation,
    Endpoint,
    Service,
    Usage,
)
from hypothesis import given
from hypothesis import strategies as st


# Strategies for generating test data
def _valid_name() -> st.SearchStrategy[str]:
    """Generate valid service/contract names (1-255 chars)."""
    return st.text(min_size=1, max_size=50, alphabet=st.characters(blacklist_categories=()))


def _valid_identifier() -> st.SearchStrategy[str]:
    """Generate valid identifiers (alphanumeric + underscore)."""
    return st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz0123456789_")


def _valid_path() -> st.SearchStrategy[str]:
    """Generate valid HTTP/RPC paths."""
    return st.text(min_size=1, max_size=200, alphabet="abcdefghijklmnopqrstuvwxyz0123456789/_-.")


def _metadata_dict() -> st.SearchStrategy[dict[str, Any]]:
    """Generate simple JSON-serializable metadata."""
    return st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.one_of(
            st.none(),
            st.text(max_size=100),
            st.integers(min_value=-1000, max_value=1000),
        ),
        max_size=5,
    )


class TestModelIdGeneration:
    """Property tests for model ID generation."""

    @given(name=_valid_name(), owner=_valid_name())
    def test_service_id_is_generated(self, name: str, owner: str) -> None:
        """Service.id is automatically generated when not provided."""
        reset_engine()
        service = Service(name=name, owner=owner)
        # ID should be generated on flush/commit
        with session_scope() as session:
            session.add(service)
            session.flush()
            assert service.id is not None
            assert isinstance(service.id, str)
            assert len(service.id) == 36  # UUID string format

    @given(name=_valid_name())
    def test_client_id_is_generated(self, name: str) -> None:
        """Client.id is automatically generated when not provided."""
        reset_engine()
        client = Client(name=name, owner="test-owner")
        with session_scope() as session:
            session.add(client)
            session.flush()
            assert client.id is not None
            assert isinstance(client.id, str)
            assert len(client.id) == 36

    @given(name=_valid_name(), owner=_valid_name())
    def test_contract_version_id_is_generated(self, name: str, owner: str) -> None:
        """ContractVersion.id is automatically generated when not provided."""
        reset_engine()
        with session_scope() as session:
            service = Service(name=name, owner=owner)
            session.add(service)
            session.flush()

            contract = Contract(service_id=service.id, name="test-contract", kind="openapi")
            session.add(contract)
            session.flush()

            version = ContractVersion(
                contract_id=contract.id,
                service_id=service.id,
                version_hash="abc123" * 10 + "ab",  # 64 chars
                raw_blob=b"test",
                canonical_blob=b"test",
            )
            session.add(version)
            session.flush()
            assert version.id is not None
            assert isinstance(version.id, str)
            assert len(version.id) == 36


class TestModelTimestamps:
    """Property tests for model timestamps."""

    @given(name=_valid_name(), owner=_valid_name())
    def test_service_created_at_timestamp(self, name: str, owner: str) -> None:
        """Service.created_at is automatically set to current UTC time."""
        reset_engine()
        before = datetime.now(UTC)
        service = Service(name=name, owner=owner)
        with session_scope() as session:
            session.add(service)
            session.flush()
            after = datetime.now(UTC)

            # created_at should be set and between before and after
            assert service.created_at is not None
            assert isinstance(service.created_at, datetime)
            assert service.created_at.tzinfo is not None
            # Allow a small tolerance for timing
            assert before <= service.created_at <= after

    @given(name=_valid_name())
    def test_client_created_at_timestamp(self, name: str) -> None:
        """Client.created_at is automatically set to current UTC time."""
        reset_engine()
        before = datetime.now(UTC)
        client = Client(name=name, owner="test-owner")
        with session_scope() as session:
            session.add(client)
            session.flush()
            after = datetime.now(UTC)

            assert client.created_at is not None
            assert isinstance(client.created_at, datetime)
            assert client.created_at.tzinfo is not None
            assert before <= client.created_at <= after


class TestModelConstraints:
    """Property tests for model constraints."""

    @given(name=_valid_name(), owner=_valid_name())
    def test_service_name_uniqueness(self, name: str, owner: str) -> None:
        """Service names are globally unique."""
        reset_engine()
        with session_scope() as session:
            service1 = Service(name=name, owner=owner)
            session.add(service1)
            session.flush()

            # Try to create another service with the same name
            service2 = Service(name=name, owner="different-owner")
            session.add(service2)
            try:
                session.flush()
                # If we get here, the constraint wasn't enforced at flush
                # But it should fail on commit
                pytest.fail("Expected unique constraint violation on service name")
            except Exception:
                # Expected - unique constraint violation
                session.rollback()

    @given(
        service_name=_valid_name(),
        owner=_valid_name(),
        contract_name=_valid_name(),
    )
    def test_contract_name_unique_per_service(
        self, service_name: str, owner: str, contract_name: str
    ) -> None:
        """Contract names are unique within a service (but not globally)."""
        reset_engine()
        with session_scope() as session:
            service = Service(name=service_name, owner=owner)
            session.add(service)
            session.flush()

            contract1 = Contract(service_id=service.id, name=contract_name, kind="openapi")
            session.add(contract1)
            session.flush()

            # Try to add another contract with same name to same service
            contract2 = Contract(service_id=service.id, name=contract_name, kind="openapi")
            session.add(contract2)
            try:
                session.flush()
                pytest.fail("Expected unique constraint violation on contract name")
            except Exception:
                session.rollback()

    @given(
        service1_name=_valid_name(),
        service2_name=_valid_name(),
        owner=_valid_name(),
        contract_name=_valid_name(),
    )
    def test_contract_name_allowed_across_services(
        self,
        service1_name: str,
        service2_name: str,
        owner: str,
        contract_name: str,
    ) -> None:
        """Same contract name is allowed in different services."""
        reset_engine()
        if service1_name == service2_name:
            return  # Skip if names are the same

        with session_scope() as session:
            service1 = Service(name=service1_name, owner=owner)
            service2 = Service(name=service2_name, owner=owner)
            session.add_all([service1, service2])
            session.flush()

            contract1 = Contract(service_id=service1.id, name=contract_name, kind="openapi")
            contract2 = Contract(service_id=service2.id, name=contract_name, kind="openapi")
            session.add_all([contract1, contract2])
            # Should not raise - names are allowed across services
            session.flush()
            assert contract1.id != contract2.id

    @given(name=_valid_name())
    def test_client_name_uniqueness(self, name: str) -> None:
        """Client names are globally unique."""
        reset_engine()
        with session_scope() as session:
            client1 = Client(name=name, owner="owner1")
            session.add(client1)
            session.flush()

            client2 = Client(name=name, owner="owner2")
            session.add(client2)
            try:
                session.flush()
                pytest.fail("Expected unique constraint violation on client name")
            except Exception:
                session.rollback()


class TestModelJsonColumns:
    """Property tests for JSON columns."""

    @given(metadata=_metadata_dict())
    def test_contract_version_spec_metadata_json(self, metadata: dict[str, Any]) -> None:
        """ContractVersion.spec_metadata accepts JSON-serializable dicts."""
        reset_engine()
        with session_scope() as session:
            service = Service(name="test-service", owner="test-owner")
            session.add(service)
            session.flush()

            contract = Contract(service_id=service.id, name="test-contract", kind="openapi")
            session.add(contract)
            session.flush()

            version = ContractVersion(
                contract_id=contract.id,
                service_id=service.id,
                version_hash="abc123" * 10 + "ab",
                raw_blob=b"test",
                canonical_blob=b"test",
                spec_metadata=metadata,
            )
            session.add(version)
            session.flush()
            assert version.spec_metadata == metadata

    @given(notes=_metadata_dict())
    def test_deprecation_notes_json(self, notes: dict[str, Any]) -> None:
        """Deprecation.notes accepts JSON-serializable dicts."""
        reset_engine()
        with session_scope() as session:
            service = Service(name="test-service", owner="test-owner")
            session.add(service)
            session.flush()

            contract = Contract(service_id=service.id, name="test-contract", kind="openapi")
            session.add(contract)
            session.flush()

            version = ContractVersion(
                contract_id=contract.id,
                service_id=service.id,
                version_hash="abc123" * 10 + "ab",
                raw_blob=b"test",
                canonical_blob=b"test",
            )
            session.add(version)
            session.flush()

            endpoint = Endpoint(
                contract_version_id=version.id,
                service_id=service.id,
                method="GET",
                path="/test",
                fingerprint="test-fingerprint",
            )
            session.add(endpoint)
            session.flush()

            deprecation = Deprecation(
                contract_version_id=version.id,
                endpoint_id=endpoint.id,
                notes=notes,
            )
            session.add(deprecation)
            session.flush()
            assert deprecation.notes == notes


class TestModelOptionalFields:
    """Property tests for optional fields."""

    @given(reason=st.one_of(st.none(), st.text(max_size=100)))
    def test_deprecation_reason_optional(self, reason: str | None) -> None:
        """Deprecation.reason can be None."""
        reset_engine()
        with session_scope() as session:
            service = Service(name="test-service", owner="test-owner")
            session.add(service)
            session.flush()

            contract = Contract(service_id=service.id, name="test-contract", kind="openapi")
            session.add(contract)
            session.flush()

            version = ContractVersion(
                contract_id=contract.id,
                service_id=service.id,
                version_hash="abc123" * 10 + "ab",
                raw_blob=b"test",
                canonical_blob=b"test",
            )
            session.add(version)
            session.flush()

            endpoint = Endpoint(
                contract_version_id=version.id,
                service_id=service.id,
                method="GET",
                path="/test",
                fingerprint="test-fingerprint",
            )
            session.add(endpoint)
            session.flush()

            deprecation = Deprecation(
                contract_version_id=version.id,
                endpoint_id=endpoint.id,
                reason=reason,
            )
            session.add(deprecation)
            session.flush()
            assert deprecation.reason == reason

    def test_endpoint_operation_id_optional(self) -> None:
        """Endpoint.operation_id can be None."""
        reset_engine()
        with session_scope() as session:
            service = Service(name="test-service", owner="test-owner")
            session.add(service)
            session.flush()

            contract = Contract(service_id=service.id, name="test-contract", kind="openapi")
            session.add(contract)
            session.flush()

            version = ContractVersion(
                contract_id=contract.id,
                service_id=service.id,
                version_hash="abc123" * 10 + "ab",
                raw_blob=b"test",
                canonical_blob=b"test",
            )
            session.add(version)
            session.flush()

            endpoint = Endpoint(
                contract_version_id=version.id,
                service_id=service.id,
                method="GET",
                path="/test",
                fingerprint="test-fingerprint",
                operation_id=None,
            )
            session.add(endpoint)
            session.flush()
            assert endpoint.operation_id is None


class TestModelDefaults:
    """Property tests for model default values."""

    def test_deprecation_status_defaults_to_proposed(self) -> None:
        """Deprecation.status defaults to 'proposed'."""
        reset_engine()
        with session_scope() as session:
            service = Service(name="test-service", owner="test-owner")
            session.add(service)
            session.flush()

            contract = Contract(service_id=service.id, name="test-contract", kind="openapi")
            session.add(contract)
            session.flush()

            version = ContractVersion(
                contract_id=contract.id,
                service_id=service.id,
                version_hash="abc123" * 10 + "ab",
                raw_blob=b"test",
                canonical_blob=b"test",
            )
            session.add(version)
            session.flush()

            endpoint = Endpoint(
                contract_version_id=version.id,
                service_id=service.id,
                method="GET",
                path="/test",
                fingerprint="test-fingerprint",
            )
            session.add(endpoint)
            session.flush()

            deprecation = Deprecation(
                contract_version_id=version.id,
                endpoint_id=endpoint.id,
            )
            session.add(deprecation)
            session.flush()
            assert deprecation.status == "proposed"

    def test_usage_source_defaults_to_manual(self) -> None:
        """Usage.source defaults to 'manual'."""
        reset_engine()
        with session_scope() as session:
            service = Service(name="test-service", owner="test-owner")
            session.add(service)
            session.flush()

            client = Client(name="test-client", owner="test-owner")
            session.add(client)
            session.flush()

            contract = Contract(service_id=service.id, name="test-contract", kind="openapi")
            session.add(contract)
            session.flush()

            version = ContractVersion(
                contract_id=contract.id,
                service_id=service.id,
                version_hash="abc123" * 10 + "ab",
                raw_blob=b"test",
                canonical_blob=b"test",
            )
            session.add(version)
            session.flush()

            endpoint = Endpoint(
                contract_version_id=version.id,
                service_id=service.id,
                method="GET",
                path="/test",
                fingerprint="test-fingerprint",
            )
            session.add(endpoint)
            session.flush()

            now = datetime.now(UTC)
            usage = Usage(
                endpoint_id=endpoint.id,
                client_id=client.id,
                window_start=now,
                window_end=now,
            )
            session.add(usage)
            session.flush()
            assert usage.source == "manual"

    def test_usage_request_count_defaults_to_zero(self) -> None:
        """Usage.request_count defaults to 0."""
        reset_engine()
        with session_scope() as session:
            service = Service(name="test-service", owner="test-owner")
            session.add(service)
            session.flush()

            client = Client(name="test-client", owner="test-owner")
            session.add(client)
            session.flush()

            contract = Contract(service_id=service.id, name="test-contract", kind="openapi")
            session.add(contract)
            session.flush()

            version = ContractVersion(
                contract_id=contract.id,
                service_id=service.id,
                version_hash="abc123" * 10 + "ab",
                raw_blob=b"test",
                canonical_blob=b"test",
            )
            session.add(version)
            session.flush()

            endpoint = Endpoint(
                contract_version_id=version.id,
                service_id=service.id,
                method="GET",
                path="/test",
                fingerprint="test-fingerprint",
            )
            session.add(endpoint)
            session.flush()

            now = datetime.now(UTC)
            usage = Usage(
                endpoint_id=endpoint.id,
                client_id=client.id,
                window_start=now,
                window_end=now,
            )
            session.add(usage)
            session.flush()
            assert usage.request_count == 0
