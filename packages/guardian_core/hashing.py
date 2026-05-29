"""Canonicalization and version hashing for OpenAPI and Protobuf contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

ContractKind = Literal["openapi", "proto"]


def canonicalize_openapi(spec: dict[str, Any]) -> bytes:
    """Return canonical UTF-8 JSON bytes of an OpenAPI spec (sorted keys)."""
    return json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonicalize_proto(raw: bytes) -> bytes:
    """Return canonical bytes for a Protobuf FileDescriptorSet.

    The input is expected to already be a serialized FileDescriptorSet; we
    pass it through unchanged so future milestones can plug in a real
    re-serializer. The hash is taken over these bytes verbatim.
    """
    return bytes(raw)


def compute_version_hash(canonical_bytes: bytes) -> str:
    """Return the sha256 hex digest of canonical contract bytes."""
    return hashlib.sha256(canonical_bytes).hexdigest()
