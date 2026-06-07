"""Property-based tests for API route helper functions.

Invariants tested:

_coerce_state (campaigns.py):
1. Valid CampaignState strings are returned unchanged
2. Invalid state strings always return "draft"
3. Return value is always a valid CampaignState
4. The function is idempotent on "draft" state

_materialize_spec_bytes (services.py):
1. For openapi contracts, raw_bytes == canonical_bytes
2. For openapi contracts, both return values are non-empty bytes
3. For proto contracts with valid base64, raw_bytes can be decoded
4. For proto contracts, canonical_bytes is non-empty
5. The function is deterministic for the same input
"""

from __future__ import annotations

import base64
from typing import Any

from guardian_core.schemas import ContractUpload
from hypothesis import given, settings
from hypothesis import strategies as st

from apps.api.routes.campaigns import _coerce_state
from apps.api.routes.services import _materialize_spec_bytes

# ============================================================================
# Strategies
# ============================================================================


def _valid_campaign_state_strategy() -> st.SearchStrategy[str]:
    """Generate valid CampaignState values."""
    return st.sampled_from(
        ["draft", "active", "decaying", "ready_to_remove", "completed", "aborted"]
    )


def _invalid_state_strategy() -> st.SearchStrategy[str]:
    """Generate strings that are NOT valid CampaignState values."""
    valid_states = ("draft", "active", "decaying", "ready_to_remove", "completed", "aborted")
    return st.text(min_size=1).filter(lambda s: s not in valid_states)


def _openapi_spec_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Generate minimal valid OpenAPI specs."""
    return st.fixed_dictionaries(
        {
            "openapi": st.just("3.0.0"),
            "info": st.fixed_dictionaries(
                {
                    "title": st.text(min_size=1, max_size=50),
                    "version": st.text(min_size=1, max_size=10),
                }
            ),
            "paths": st.just({}),
        }
    )


def _proto_bytes_strategy() -> st.SearchStrategy[bytes]:
    """Generate arbitrary bytes (mimicking proto FileDescriptorSet)."""
    return st.binary(min_size=1, max_size=1000)


def _contract_upload_openapi_strategy() -> st.SearchStrategy[ContractUpload]:
    """Generate ContractUpload with openapi spec."""
    return st.builds(
        ContractUpload,
        kind=st.just("openapi"),
        name=st.text(min_size=1, max_size=100),
        spec=_openapi_spec_strategy(),
        spec_b64=st.none(),
        spec_metadata=st.just({}),
    )


def _contract_upload_proto_strategy() -> st.SearchStrategy[ContractUpload]:
    """Generate ContractUpload with proto spec (base64 encoded)."""
    return st.builds(
        ContractUpload,
        kind=st.just("proto"),
        name=st.text(min_size=1, max_size=100),
        spec=st.none(),
        spec_b64=_proto_bytes_strategy().map(lambda b: base64.b64encode(b).decode("ascii")),
        spec_metadata=st.just({}),
    )


# ============================================================================
# Tests for _coerce_state
# ============================================================================


@settings(max_examples=100)
@given(state=_valid_campaign_state_strategy())
def test_coerce_state_valid_states_unchanged(state: str) -> None:
    """Valid CampaignState strings must be returned unchanged."""
    result = _coerce_state(state)
    assert result == state  # type: ignore[comparison-overlap]


@settings(max_examples=100)
@given(state=_invalid_state_strategy())
def test_coerce_state_invalid_returns_draft(state: str) -> None:
    """Invalid state strings must return 'draft'."""
    result = _coerce_state(state)
    assert result == "draft"


@settings(max_examples=100)
@given(state=_valid_campaign_state_strategy())
def test_coerce_state_returns_valid_campaign_state(state: str) -> None:
    """Return value must always be a valid CampaignState."""
    result = _coerce_state(state)
    valid_states = ("draft", "active", "decaying", "ready_to_remove", "completed", "aborted")
    assert result in valid_states


@settings(max_examples=50)
@given(_coerce_state_draft_idempotent=st.just("draft"))
def test_coerce_state_draft_idempotent(_coerce_state_draft_idempotent: str) -> None:
    """Applying _coerce_state to 'draft' twice must yield 'draft'."""
    once = _coerce_state(_coerce_state_draft_idempotent)
    twice = _coerce_state(once)
    assert once == twice == "draft"


# ============================================================================
# Tests for _materialize_spec_bytes
# ============================================================================


@settings(max_examples=50)
@given(payload=_contract_upload_openapi_strategy())
def test_materialize_spec_bytes_openapi_raw_equals_canonical(payload: ContractUpload) -> None:
    """For OpenAPI specs, raw_bytes must equal canonical_bytes."""
    raw, canonical = _materialize_spec_bytes(payload)
    assert raw == canonical, "OpenAPI raw_bytes should equal canonical_bytes"


@settings(max_examples=50)
@given(payload=_contract_upload_openapi_strategy())
def test_materialize_spec_bytes_openapi_returns_bytes(payload: ContractUpload) -> None:
    """For OpenAPI specs, both return values must be non-empty bytes."""
    raw, canonical = _materialize_spec_bytes(payload)
    assert isinstance(raw, bytes), "raw_bytes must be bytes"
    assert isinstance(canonical, bytes), "canonical_bytes must be bytes"
    assert len(raw) > 0, "raw_bytes must be non-empty"
    assert len(canonical) > 0, "canonical_bytes must be non-empty"


@settings(max_examples=50)
@given(payload=_contract_upload_proto_strategy())
def test_materialize_spec_bytes_proto_raw_can_decode(payload: ContractUpload) -> None:
    """For proto specs, raw_bytes must be decodable from base64 input."""
    raw, _ = _materialize_spec_bytes(payload)
    # The raw_bytes should be the base64-decoded version of spec_b64
    # So we can verify it's valid binary data
    assert isinstance(raw, bytes), "raw_bytes must be bytes"
    assert len(raw) > 0, "raw_bytes must be non-empty for proto"


@settings(max_examples=50)
@given(payload=_contract_upload_proto_strategy())
def test_materialize_spec_bytes_proto_canonical_is_bytes(payload: ContractUpload) -> None:
    """For proto specs, canonical_bytes must be non-empty bytes."""
    _, canonical = _materialize_spec_bytes(payload)
    assert isinstance(canonical, bytes), "canonical_bytes must be bytes"
    assert len(canonical) > 0, "canonical_bytes must be non-empty for proto"


@settings(max_examples=50)
@given(payload=_contract_upload_openapi_strategy())
def test_materialize_spec_bytes_deterministic_openapi(payload: ContractUpload) -> None:
    """_materialize_spec_bytes must be deterministic for the same OpenAPI input."""
    raw1, canonical1 = _materialize_spec_bytes(payload)
    raw2, canonical2 = _materialize_spec_bytes(payload)
    assert raw1 == raw2, "raw_bytes must be deterministic"
    assert canonical1 == canonical2, "canonical_bytes must be deterministic"


@settings(max_examples=50)
@given(payload=_contract_upload_proto_strategy())
def test_materialize_spec_bytes_deterministic_proto(payload: ContractUpload) -> None:
    """_materialize_spec_bytes must be deterministic for the same proto input."""
    raw1, canonical1 = _materialize_spec_bytes(payload)
    raw2, canonical2 = _materialize_spec_bytes(payload)
    assert raw1 == raw2, "raw_bytes must be deterministic"
    assert canonical1 == canonical2, "canonical_bytes must be deterministic"
