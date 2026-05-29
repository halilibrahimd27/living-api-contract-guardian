"""Unit tests for canonicalization and version hashing."""

from __future__ import annotations

import hashlib
import json

from guardian_core.hashing import (
    canonicalize_openapi,
    canonicalize_proto,
    compute_version_hash,
)


def test_openapi_canonicalization_is_key_order_invariant() -> None:
    a = {"openapi": "3.0.0", "info": {"title": "x", "version": "1"}}
    b = {"info": {"version": "1", "title": "x"}, "openapi": "3.0.0"}
    assert canonicalize_openapi(a) == canonicalize_openapi(b)


def test_version_hash_is_sha256_of_canonical_bytes() -> None:
    spec = {"openapi": "3.0.0"}
    canonical = canonicalize_openapi(spec)
    assert compute_version_hash(canonical) == hashlib.sha256(canonical).hexdigest()


def test_openapi_canonical_matches_sorted_json_dumps() -> None:
    spec = {"b": 1, "a": [3, 2, 1]}
    assert (
        canonicalize_openapi(spec)
        == json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    )


def test_proto_canonical_is_passthrough() -> None:
    raw = b"\x00\x01\x02\xff"
    assert canonicalize_proto(raw) == raw
    assert compute_version_hash(canonicalize_proto(raw)) == hashlib.sha256(raw).hexdigest()
