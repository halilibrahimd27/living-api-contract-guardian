"""Join raw changes against the ``inferred_endpoints`` catalogue.

Each :class:`~guardian_diff.models.RawChange` carries a ``location`` that
embeds an HTTP method + OpenAPI-style path template (for OpenAPI) or a
``service.method`` pair (for protobuf). Mined client call sites
(``InferredEndpoint`` rows) carry the same ``(method, path_template)``
fingerprint, so the join is a simple equality on those two columns.

For protobuf changes, the static miner records gRPC stub calls under
their fully-qualified ``service.method`` name in ``path_template`` with
``method='POST'`` — see ``guardian_core.mining.python_visitor``. We join
on the rpc fully-qualified name embedded in ``RawChange.detail``.

The result is a *set of repos* affected by each change. The granular
client name is repo-level rather than per-call-site because operators
care about "which downstream codebase do I tell to upgrade?", not
"which call site inside that codebase".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from guardian_core.models import InferredEndpoint
from sqlalchemy import select
from sqlalchemy.orm import Session

from guardian_diff.models import RawChange


def _endpoint_keys_from_change(change: RawChange) -> list[tuple[str, str]]:
    """Return the list of ``(method, path_template)`` keys a change affects.

    Path-level changes (``openapi.path.added`` / ``...removed``) expand
    to one key per method present on the path, since each method is a
    separately mineable call-site signature in the static client
    catalogue.
    """
    detail: dict[str, Any] = change.detail
    method = detail.get("method")
    path = detail.get("path")
    methods = detail.get("methods")
    keys: list[tuple[str, str]] = []
    if isinstance(method, str) and isinstance(path, str):
        keys.append((method.upper(), path))
    elif isinstance(path, str) and isinstance(methods, list) and methods:
        for m in methods:
            if isinstance(m, str):
                keys.append((m.upper(), path))
    # Protobuf RPC: detail carries service + method; the mined client
    # path template is ``service.method`` (a single dotted identifier).
    if change.kind.startswith("proto.rpc.") or change.kind.startswith("proto.service."):
        service = detail.get("service")
        rpc_method = detail.get("method")
        if isinstance(service, str) and isinstance(rpc_method, str):
            keys.append(("POST", f"{service}.{rpc_method}"))
    return keys


def affected_clients(session: Session, changes: Iterable[RawChange]) -> dict[int, list[str]]:
    """Return a map of ``id(change) -> [repo, …]``.

    Uses a single SQL query per unique ``(method, path_template)`` pair,
    so the cost is O(unique-locations) not O(changes). The returned
    keys are ``id(change)`` because :class:`RawChange` is hashable only
    by Pydantic-default equality, which would collapse two genuinely
    distinct events with identical content.
    """
    keys_by_change: dict[int, list[tuple[str, str]]] = {}
    for change in changes:
        keys = _endpoint_keys_from_change(change)
        if keys:
            keys_by_change[id(change)] = keys

    unique_keys = {k for keys in keys_by_change.values() for k in keys}
    repos_by_key: dict[tuple[str, str], list[str]] = {k: [] for k in unique_keys}
    for method, path in unique_keys:
        rows = session.execute(
            select(InferredEndpoint.repo)
            .where(InferredEndpoint.method == method)
            .where(InferredEndpoint.path_template == path)
            .distinct()
        ).all()
        repos_by_key[(method, path)] = sorted({row[0] for row in rows})

    out: dict[int, list[str]] = {}
    for change_id, keys in keys_by_change.items():
        merged: set[str] = set()
        for k in keys:
            merged.update(repos_by_key.get(k, []))
        out[change_id] = sorted(merged)
    return out
