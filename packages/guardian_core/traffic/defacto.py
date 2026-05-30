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
            json_ct["schema"] = _merge_schemas(json_ct.get("schema"), req_schema)
        if resp_schema:
            responses = op.setdefault("responses", {})
            status_obj = responses.setdefault("200", {"description": "observed"})
            content = status_obj.setdefault("content", {})
            json_ct = content.setdefault("application/json", {})
            json_ct["schema"] = _merge_schemas(json_ct.get("schema"), resp_schema)

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


def _merge_schemas(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge two JSON schemas, preferring observed types as a union.

    Real schema-merging is a deep topic; we take a pragmatic stance:
    - if both are object schemas, union their ``properties`` and
      ``required`` (intersection of required-sets, to match union samples);
    - otherwise prefer the observed schema and fall back to existing for
      missing keys.
    """
    if not existing:
        return dict(incoming)
    if existing.get("type") == "object" and incoming.get("type") == "object":
        merged: dict[str, Any] = dict(existing)
        merged["type"] = "object"
        e_props = existing.get("properties") or {}
        i_props = incoming.get("properties") or {}
        all_keys = set(e_props.keys()) | set(i_props.keys())
        merged_props: dict[str, Any] = {}
        for k in sorted(all_keys):
            if k in e_props and k in i_props:
                merged_props[k] = _merge_schemas(e_props[k], i_props[k])
            else:
                merged_props[k] = e_props.get(k) or i_props.get(k) or {}
        merged["properties"] = merged_props
        e_req = set(existing.get("required") or [])
        i_req = set(incoming.get("required") or [])
        if e_req or i_req:
            inter = sorted(e_req & i_req) if (e_req and i_req) else sorted(e_req | i_req)
            merged["required"] = inter
        return merged
    # Different shapes — keep the incoming schema but preserve existing
    # metadata keys (e.g. ``description``) that incoming doesn't override.
    merged = dict(existing)
    merged.update(incoming)
    return merged
