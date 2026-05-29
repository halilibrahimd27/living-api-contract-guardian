"""Property-based tests for contract canonicalization and version hashing.

Invariants tested:
1. OpenAPI canonicalization is deterministic and independent of key order
2. Proto canonicalization is a passthrough that preserves bytes
3. Version hashes are deterministic SHA256 hex digests of exactly 64 chars
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from guardian_core.hashing import (
    canonicalize_openapi,
    canonicalize_proto,
    compute_version_hash,
)
from hypothesis import given
from hypothesis import strategies as st


# Strategies for generating test data
def _openapi_dict() -> st.SearchStrategy[dict[str, Any]]:
    """Generate valid OpenAPI-like JSON-serializable dictionaries."""
    return st.dictionaries(
        keys=st.text(min_size=1, max_size=50).filter(lambda s: s.isidentifier()),
        values=st.one_of(
            st.none(),
            st.booleans(),
            st.integers(min_value=-1000000, max_value=1000000),
            st.floats(allow_nan=False, allow_infinity=False),
            st.text(max_size=100),
            st.lists(st.integers(min_value=-1000, max_value=1000), max_size=10),
            st.lists(st.text(max_size=50), max_size=10),
        ),
        max_size=20,
    )


class TestOpenAPICanonicaliza:
    """Property tests for OpenAPI canonicalization."""

    @given(_openapi_dict())
    def test_openapi_canonicalization_is_deterministic(self, spec: dict[str, Any]) -> None:
        """Canonicalizing the same spec twice produces identical output."""
        result1 = canonicalize_openapi(spec)
        result2 = canonicalize_openapi(spec)
        assert result1 == result2
        assert isinstance(result1, bytes)

    @given(st.just({"a": 1, "b": 2}))
    def test_openapi_canonicalization_ignores_key_order(self, _: dict[str, Any]) -> None:
        """Different insertion orders produce the same canonical form."""
        spec1 = {"z": 26, "a": 1, "m": 13}
        spec2 = {"a": 1, "m": 13, "z": 26}
        spec3 = {"m": 13, "z": 26, "a": 1}
        assert canonicalize_openapi(spec1) == canonicalize_openapi(spec2)
        assert canonicalize_openapi(spec2) == canonicalize_openapi(spec3)

    @given(_openapi_dict())
    def test_openapi_canonical_is_valid_utf8(self, spec: dict[str, Any]) -> None:
        """Canonical form is valid UTF-8 bytes that can be decoded."""
        canonical = canonicalize_openapi(spec)
        # Should not raise
        decoded = canonical.decode("utf-8")
        assert isinstance(decoded, str)

    @given(_openapi_dict())
    def test_openapi_canonical_roundtrip(self, spec: dict[str, Any]) -> None:
        """Canonicalizing a spec, parsing it back, and canonicalizing again yields the same bytes."""
        canonical1 = canonicalize_openapi(spec)
        parsed = json.loads(canonical1.decode("utf-8"))
        canonical2 = canonicalize_openapi(parsed)
        assert canonical1 == canonical2

    @given(_openapi_dict())
    def test_openapi_canonical_uses_sort_keys(self, spec: dict[str, Any]) -> None:
        """Canonical form matches json.dumps with sort_keys=True."""
        canonical = canonicalize_openapi(spec)
        expected = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        assert canonical == expected


class TestProtoCanonicaliza:
    """Property tests for Protobuf canonicalization."""

    @given(st.binary(min_size=0, max_size=1000))
    def test_proto_canonicalization_is_passthrough(self, raw: bytes) -> None:
        """Proto canonicalization returns the input bytes unchanged."""
        canonical = canonicalize_proto(raw)
        assert canonical == raw
        assert isinstance(canonical, bytes)

    @given(st.binary(min_size=0, max_size=1000))
    def test_proto_canonicalization_is_deterministic(self, raw: bytes) -> None:
        """Proto canonicalizing the same bytes twice yields identical output."""
        result1 = canonicalize_proto(raw)
        result2 = canonicalize_proto(raw)
        assert result1 == result2

    @given(st.binary(min_size=0, max_size=1000))
    def test_proto_canonical_preserves_length(self, raw: bytes) -> None:
        """Proto canonicalization preserves the length of input bytes."""
        canonical = canonicalize_proto(raw)
        assert len(canonical) == len(raw)


class TestVersionHashing:
    """Property tests for version hash computation."""

    @given(st.binary(min_size=1, max_size=10000))
    def test_version_hash_is_64_hex_chars(self, canonical_bytes: bytes) -> None:
        """Version hash is always a 64-character lowercase hex string (SHA256)."""
        hash_str = compute_version_hash(canonical_bytes)
        assert isinstance(hash_str, str)
        assert len(hash_str) == 64
        # All characters are hex digits
        assert all(c in "0123456789abcdef" for c in hash_str)

    @given(st.binary(min_size=1, max_size=10000))
    def test_version_hash_is_deterministic(self, canonical_bytes: bytes) -> None:
        """Hashing the same bytes twice produces the same result."""
        hash1 = compute_version_hash(canonical_bytes)
        hash2 = compute_version_hash(canonical_bytes)
        assert hash1 == hash2

    @given(st.binary(min_size=1, max_size=10000))
    def test_version_hash_matches_sha256_hexdigest(self, canonical_bytes: bytes) -> None:
        """Version hash equals the SHA256 hex digest of the input."""
        hash_str = compute_version_hash(canonical_bytes)
        expected = hashlib.sha256(canonical_bytes).hexdigest()
        assert hash_str == expected

    @given(
        st.lists(
            st.binary(min_size=1, max_size=100),
            min_size=2,
            max_size=10,
            unique=True,
        )
    )
    def test_different_inputs_produce_different_hashes(self, byte_inputs: list[bytes]) -> None:
        """Different byte inputs produce different hashes (collision resistance)."""
        hashes = [compute_version_hash(b) for b in byte_inputs]
        # All hashes should be unique
        assert len(hashes) == len(set(hashes))


class TestHashingIntegration:
    """Property tests for integration between canonicalization and hashing."""

    @given(_openapi_dict())
    def test_openapi_hash_is_hash_of_canonical(self, spec: dict[str, Any]) -> None:
        """Hash of an OpenAPI spec equals hash of its canonical form."""
        canonical = canonicalize_openapi(spec)
        hash1 = compute_version_hash(canonical)
        hash2 = hashlib.sha256(canonical).hexdigest()
        assert hash1 == hash2

    @given(st.binary(min_size=1, max_size=1000))
    def test_proto_hash_is_hash_of_canonical(self, raw: bytes) -> None:
        """Hash of proto bytes equals hash of its canonical form."""
        canonical = canonicalize_proto(raw)
        hash_result = compute_version_hash(canonical)
        expected = hashlib.sha256(canonical).hexdigest()
        assert hash_result == expected
