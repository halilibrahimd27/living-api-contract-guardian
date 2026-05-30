"""Shared JSON-schema merge helper for the traffic pipeline.

Both the ingestor (when rolling up new samples into an existing
``observed_endpoints`` row) and the defacto materializer (when fusing the
static OpenAPI spec with the observed traffic) need a pragmatic
schema-union operation. This module is the single source of truth.

The merger is intentionally narrow:

  * For two ``type: object`` schemas it unions ``properties`` (recursing
    per key) and intersects ``required`` (a field is only "required" if
    every observed sample required it).
  * For everything else it returns a shallow ``existing | incoming``
    overlay so non-conflicting metadata (``description``, ``title``, …)
    on the static side survives.

The module name starts with ``_`` to mark it as a package-internal
helper — it has no public re-export and is not part of the API surface.
"""

from __future__ import annotations

from typing import Any


def merge_json_schemas(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """Return a pragmatic union of two JSON-Schema dicts.

    ``existing`` may be ``None``/empty (first observation); in that case
    the incoming schema is returned unchanged.
    """
    if not existing:
        return dict(incoming)
    if not incoming:
        return dict(existing)
    if existing.get("type") == "object" and incoming.get("type") == "object":
        merged: dict[str, Any] = dict(existing)
        merged["type"] = "object"
        e_props = existing.get("properties") or {}
        i_props = incoming.get("properties") or {}
        merged_props: dict[str, Any] = {}
        for key in sorted(set(e_props.keys()) | set(i_props.keys())):
            if key in e_props and key in i_props:
                merged_props[key] = merge_json_schemas(e_props[key], i_props[key])
            else:
                merged_props[key] = e_props.get(key) or i_props.get(key) or {}
        merged["properties"] = merged_props
        # ``required`` semantics: distinguish "key absent" (no opinion) from
        # "key present, possibly empty" (explicitly declared). Intersect only
        # when both sides made an explicit declaration; otherwise keep the
        # side that did.
        e_has_required = "required" in existing
        i_has_required = "required" in incoming
        if e_has_required and i_has_required:
            e_req = set(existing.get("required") or [])
            i_req = set(incoming.get("required") or [])
            merged["required"] = sorted(e_req & i_req)
        elif e_has_required:
            merged["required"] = sorted(set(existing.get("required") or []))
        elif i_has_required:
            merged["required"] = sorted(set(incoming.get("required") or []))
        return merged
    # Different shapes: keep ``existing`` metadata, let ``incoming`` win
    # on conflicting keys (most importantly ``type``).
    overlay = dict(existing)
    overlay.update(incoming)
    return overlay
