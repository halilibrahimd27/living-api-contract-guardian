"""Materialize the de-facto contract: static OpenAPI ⊕ observed traffic.

The merged document is OpenAPI-shaped (``openapi``, ``info``, ``paths``)
so downstream tooling can keep treating it as a contract spec. Each path
gets:

  * ``x-source``: ``"static"`` | ``"observed"`` | ``"both"``
  * ``x-sample-count``: total observed samples (if observed)
  * ``x-last-seen-at``: ISO timestamp of last observation (if observed)

Per-operation inferred request/response schemas are spliced into
``requestBody.content."application/json".schema`` and
``responses."200".content."application/json".schema``. We never delete
fields from the static spec; observed-only endpoints are added new.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from guardian_core.traffic._merge import merge_json_schemas


def build_defacto_contract(
    static_spec: dict[str, Any] | None,
    observed: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Return the materialized de-facto contract dict.

    ``observed`` is an iterable of dicts shaped::

        {"method": "GET",
         "path_template": "/users/{id}",
         "request_schema":  {...},
         "response_schema": {...},
         "sample_count":   42,
         "last_seen_at":   "2026-05-29T12:00:00Z",
         "matched": bool}

    ``static_spec`` is the most recent OpenAPI spec for the service, or
    ``None`` if the service has no static contract registered yet.
    """
    base = (
        copy.deepcopy(static_spec)
        if static_spec
        else {
            "openapi": "3.0.0",
            "info": {"title": "defacto", "version": "0.0.0"},
            "paths": {},
        }
    )
    base.setdefault("openapi", "3.0.0")
    base.setdefault("info", {"title": "defacto", "version": "0.0.0"})
    paths = base.setdefault("paths", {})
    if not isinstance(paths, dict):
        paths = {}
        base["paths"] = paths

    static_keys = set(paths.keys())

    for obs in observed:
        method = str(obs["method"]).lower()
        path = obs["path_template"]
        existing = paths.get(path)
        if not isinstance(existing, dict):
            existing = {}
        op = existing.get(method)
        if not isinstance(op, dict):
            op = {}

        req_schema = obs.get("request_schema") or {}
        resp_schema = obs.get("response_schema") or {}
        if req_schema:
            request_body = op.setdefault("requestBody", {})
            content = request_body.setdefault("content", {})
            json_ct = content.setdefault("application/json", {})
            json_ct["schema"] = merge_json_schemas(json_ct.get("schema"), req_schema)
        if resp_schema:
            responses = op.setdefault("responses", {})
            status_obj = responses.setdefault("200", {"description": "observed"})
            content = status_obj.setdefault("content", {})
            json_ct = content.setdefault("application/json", {})
            json_ct["schema"] = merge_json_schemas(json_ct.get("schema"), resp_schema)

        # Telemetry annotations.
        op["x-sample-count"] = int(obs.get("sample_count", 0))
        if obs.get("last_seen_at"):
            op["x-last-seen-at"] = obs["last_seen_at"]
        op["x-matched-static"] = bool(obs.get("matched", False))

        existing[method] = op
        if path in static_keys:
            existing["x-source"] = "both"
        else:
            existing.setdefault("x-source", "observed")
        paths[path] = existing

    # Paths that exist only in the static spec keep x-source="static".
    for _path, value in paths.items():
        if isinstance(value, dict) and "x-source" not in value:
            value["x-source"] = "static"

    return base
