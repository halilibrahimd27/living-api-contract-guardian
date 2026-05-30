"""Schema inference for observed JSON request/response bodies.

We feed each observed payload into a ``genson`` ``SchemaBuilder``; the
resulting JSON Schema is then post-processed to:

  * collapse ``anyOf`` / ``oneOf`` branches that only differ by ``type``
    (genson emits a list when it sees multiple primitives for one field —
    we keep that as ``type: [a, b]`` for compactness);
  * detect enums: string fields whose observed value set is small and
    bounded get an ``enum`` qualifier appended.

We also expose ``walk_field_paths`` which produces a flat list of
``(json_pointer_like_path, json_types, sample_value)`` triples — the unit
the field-usage telemetry table indexes on.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from genson import SchemaBuilder

# Maximum unique values for a string field to be classified as an enum.
ENUM_CARDINALITY_THRESHOLD = 8


def infer_schema(samples: Iterable[Any]) -> dict[str, Any]:
    """Build and return a post-processed JSON Schema for the given samples.

    ``samples`` is an iterable of decoded JSON values (dict, list, str, …).
    Returns an empty dict if no samples were given.
    """
    builder = SchemaBuilder()
    builder.add_schema({"type": "object", "properties": {}})
    saw_any = False
    string_value_index: dict[tuple[str, ...], set[str]] = {}
    string_total_seen: dict[tuple[str, ...], int] = {}
    for sample in samples:
        if sample is None:
            continue
        saw_any = True
        builder.add_object(sample)
        _index_string_values(sample, (), string_value_index, string_total_seen)
    if not saw_any:
        return {}
    schema = builder.to_schema()
    schema = _merge_anyof_branches(schema)
    schema = _detect_enums(schema, string_value_index, string_total_seen)
    # Drop the ``$schema`` URL — it isn't meaningful for our merged contract.
    schema.pop("$schema", None)
    return schema


def _index_string_values(
    value: Any,
    path: tuple[str, ...],
    index: dict[tuple[str, ...], set[str]],
    totals: dict[tuple[str, ...], int],
) -> None:
    """Walk a sample tree and record observed string values per path."""
    if isinstance(value, str):
        index.setdefault(path, set()).add(value)
        totals[path] = totals.get(path, 0) + 1
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _index_string_values(v, (*path, str(k)), index, totals)
        return
    if isinstance(value, list):
        for item in value:
            _index_string_values(item, (*path, "[]"), index, totals)


def _merge_anyof_branches(schema: dict[str, Any]) -> dict[str, Any]:
    """Collapse ``anyOf``/``oneOf`` branches that only disagree on type.

    genson emits ``{"anyOf": [{"type": "integer"}, {"type": "string"}]}``
    for primitives mixed across samples; we rewrite to
    ``{"type": ["integer", "string"]}`` so the contract stays terse.
    """
    if not isinstance(schema, dict):
        return schema
    for key in ("anyOf", "oneOf"):
        branches = schema.get(key)
        if (
            isinstance(branches, list)
            and branches
            and all(isinstance(b, dict) and set(b.keys()) <= {"type"} for b in branches)
        ):
            types: list[str] = []
            for b in branches:
                t = b.get("type")
                if isinstance(t, str) and t not in types:
                    types.append(t)
                elif isinstance(t, list):
                    for tt in t:
                        if isinstance(tt, str) and tt not in types:
                            types.append(tt)
            schema = {k: v for k, v in schema.items() if k != key}
            schema["type"] = types if len(types) > 1 else types[0]
    # Recurse into properties / items.
    if "properties" in schema and isinstance(schema["properties"], dict):
        schema["properties"] = {
            k: _merge_anyof_branches(v) for k, v in schema["properties"].items()
        }
    if "items" in schema:
        if isinstance(schema["items"], dict):
            schema["items"] = _merge_anyof_branches(schema["items"])
        elif isinstance(schema["items"], list):
            schema["items"] = [_merge_anyof_branches(x) for x in schema["items"]]
    return schema


def _detect_enums(
    schema: dict[str, Any],
    string_index: dict[tuple[str, ...], set[str]],
    totals: dict[tuple[str, ...], int],
) -> dict[str, Any]:
    """Annotate low-cardinality string fields with an ``enum`` qualifier."""

    def _recur(node: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(node, dict):
            return node
        node_type = node.get("type")
        if node_type == "string" and path in string_index:
            values = string_index[path]
            seen_total = totals.get(path, 0)
            if 1 < len(values) <= ENUM_CARDINALITY_THRESHOLD and seen_total >= len(values) * 2:
                # Each value seen at least twice on average => probable enum.
                node = dict(node)
                node["enum"] = sorted(values)
        props = node.get("properties")
        if isinstance(props, dict):
            node["properties"] = {
                k: _recur(v if isinstance(v, dict) else {}, (*path, str(k)))
                for k, v in props.items()
            }
        items = node.get("items")
        if isinstance(items, dict):
            node["items"] = _recur(items, (*path, "[]"))
        return node

    return _recur(schema, ())


def walk_field_paths(value: Any, prefix: str = "$") -> Iterator[tuple[str, str]]:
    """Yield ``(field_path, json_type)`` pairs for every leaf in a JSON value.

    Path syntax: ``$.foo.bar[*].baz`` — JSONPath-like but flattened
    (``[*]`` for arrays so we don't blow up on index differences).
    Containers also yield a row so we can count "this object exists".
    """
    json_type = _json_type_of(value)
    yield (prefix, json_type)
    if isinstance(value, dict):
        for k, v in value.items():
            yield from walk_field_paths(v, f"{prefix}.{k}")
    elif isinstance(value, list):
        for item in value:
            yield from walk_field_paths(item, f"{prefix}[*]")


def _json_type_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"
