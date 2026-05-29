"""Static AST-based client contract miner.

Scans a client repository (Python, JavaScript, TypeScript) for HTTP and
gRPC call sites and emits :class:`InferredCallSite` records describing
the inferred endpoint signature: method, OpenAPI-style path template,
and observed query/body field names.
"""

from __future__ import annotations

from guardian_core.mining.models import InferredCallSite
from guardian_core.mining.repo_scanner import (
    PersistenceResult,
    mine_repo,
    persist_call_sites,
)

__all__ = [
    "InferredCallSite",
    "PersistenceResult",
    "mine_repo",
    "persist_call_sites",
]
