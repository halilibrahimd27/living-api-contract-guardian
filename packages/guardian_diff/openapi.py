"""OpenAPI 3.x raw-change walker.

Compares two OpenAPI spec dictionaries (already JSON-decoded) and emits
a list of :class:`~guardian_diff.models.RawChange` records describing
each structural delta. The walker is intentionally conservative: it
emits one event per atomic change so the rule engine has fine-grained
hooks for verdict assignment.

We never reinvent the wheel for *semantic* OpenAPI diffing where a
dedicated tool exists (oasdiff under ``vendor/bin/oasdiff`` is the
canonical choice — see :mod:`guardian_diff.spectral`). When that binary
is available, callers are encouraged to combine its findings with this
walker's via the rule engine. Absent the vendor binary, this walker
covers the path / operation / parameter / schema surface that's needed
to satisfy the breaking-change fixture matrix.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from guardian_diff.models import RawChange

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _paths(spec: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(spec.get("paths"))


def _operations(path_item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        k: _as_dict(v)
        for k, v in path_item.items()
        if isinstance(k, str) and k.lower() in _HTTP_METHODS
    }


def _resolve_schema(spec: dict[str, Any], schema: Any) -> dict[str, Any]:
    """Best-effort one-hop $ref resolution against ``#/components/schemas``."""
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref.rsplit("/", 1)[-1]
        components = _as_dict(_as_dict(spec.get("components")).get("schemas"))
        target = components.get(name)
        if isinstance(target, dict):
            return target
        return {}
    return schema


def _request_body_schema(spec: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    body = _as_dict(op.get("requestBody"))
    content = _as_dict(body.get("content"))
    json_block = _as_dict(content.get("application/json"))
    return _resolve_schema(spec, json_block.get("schema"))


def _request_body_required(op: dict[str, Any]) -> bool:
    body = _as_dict(op.get("requestBody"))
    return bool(body.get("required", False))


def _response_schema(spec: dict[str, Any], op: dict[str, Any], code: str) -> dict[str, Any]:
    responses = _as_dict(op.get("responses"))
    block = _as_dict(responses.get(code))
    content = _as_dict(block.get("content"))
    json_block = _as_dict(content.get("application/json"))
    return _resolve_schema(spec, json_block.get("schema"))


def _params_by_key(op: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Index parameters by ``(in, name)`` — the OpenAPI canonical identity."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for p in op.get("parameters", []) or []:
        if not isinstance(p, dict):
            continue
        key = (str(p.get("in", "query")), str(p.get("name", "")))
        out[key] = p
    return out


def _param_type(param: dict[str, Any]) -> str | None:
    schema = _as_dict(param.get("schema"))
    typ = schema.get("type")
    return str(typ) if isinstance(typ, str) else None


def _diff_schema(
    location: str,
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    side: str,
) -> Iterator[RawChange]:
    """Compare two JSON-schema-shaped dicts and emit property / enum deltas.

    ``side`` is one of ``"request_body"`` or ``"response"`` and is used
    to namespace the emitted kinds (e.g. ``openapi.request_body.property.added.required``).
    """
    before_props = _as_dict(before.get("properties"))
    after_props = _as_dict(after.get("properties"))
    before_required = set(before.get("required") or [])
    after_required = set(after.get("required") or [])

    for name in sorted(set(after_props) - set(before_props)):
        is_required = name in after_required
        kind = (
            f"openapi.{side}.property.added.required"
            if is_required and side == "request_body"
            else (
                f"openapi.{side}.property.added.optional"
                if side == "request_body"
                else f"openapi.{side}.property.added"
            )
        )
        yield RawChange(
            kind=kind,
            location=f"{location}/properties/{name}",
            before=None,
            after=after_props[name],
            detail={"required": is_required},
        )

    for name in sorted(set(before_props) - set(after_props)):
        kind = f"openapi.{side}.property.removed"
        yield RawChange(
            kind=kind,
            location=f"{location}/properties/{name}",
            before=before_props[name],
            after=None,
            detail={"was_required": name in before_required},
        )

    for name in sorted(set(before_props) & set(after_props)):
        before_p = _as_dict(before_props[name])
        after_p = _as_dict(after_props[name])
        before_type = before_p.get("type")
        after_type = after_p.get("type")
        if before_type != after_type and (before_type is not None or after_type is not None):
            yield RawChange(
                kind=f"openapi.{side}.property.type_changed",
                location=f"{location}/properties/{name}",
                before=before_type,
                after=after_type,
                detail={},
            )
        was_required = name in before_required
        is_required = name in after_required
        if was_required != is_required and side == "request_body":
            kind = (
                f"openapi.{side}.property.required.increased"
                if is_required
                else f"openapi.{side}.property.required.decreased"
            )
            yield RawChange(
                kind=kind,
                location=f"{location}/properties/{name}",
                before=was_required,
                after=is_required,
                detail={},
            )
        # Enum diffs.
        before_enum = before_p.get("enum")
        after_enum = after_p.get("enum")
        if isinstance(before_enum, list) and isinstance(after_enum, list):
            before_set = {repr(x) for x in before_enum}
            after_set = {repr(x) for x in after_enum}
            for value in sorted(after_set - before_set):
                yield RawChange(
                    kind="openapi.enum.value.added",
                    location=f"{location}/properties/{name}/enum",
                    before=None,
                    after=value,
                    detail={},
                )
            for value in sorted(before_set - after_set):
                yield RawChange(
                    kind="openapi.enum.value.removed",
                    location=f"{location}/properties/{name}/enum",
                    before=value,
                    after=None,
                    detail={},
                )


def diff_openapi(before_spec: dict[str, Any], after_spec: dict[str, Any]) -> list[RawChange]:
    """Return the raw change set between two OpenAPI specs."""
    before_spec = before_spec or {}
    after_spec = after_spec or {}
    changes: list[RawChange] = []
    changes.extend(_walk_paths(before_spec, after_spec))
    changes.extend(_walk_components(before_spec, after_spec))
    return changes


def _walk_paths(before_spec: dict[str, Any], after_spec: dict[str, Any]) -> Iterator[RawChange]:
    before_paths = _paths(before_spec)
    after_paths = _paths(after_spec)
    for path in sorted(set(after_paths) - set(before_paths)):
        methods = sorted(m.upper() for m in _operations(_as_dict(after_paths[path])))
        yield RawChange(
            kind="openapi.path.added",
            location=f"/paths/{path}",
            before=None,
            after=after_paths[path],
            detail={"path": path, "methods": methods},
        )
    for path in sorted(set(before_paths) - set(after_paths)):
        methods = sorted(m.upper() for m in _operations(_as_dict(before_paths[path])))
        yield RawChange(
            kind="openapi.path.removed",
            location=f"/paths/{path}",
            before=before_paths[path],
            after=None,
            detail={"path": path, "methods": methods},
        )
    for path in sorted(set(before_paths) & set(after_paths)):
        yield from _walk_path_item(
            before_spec,
            after_spec,
            path,
            _as_dict(before_paths[path]),
            _as_dict(after_paths[path]),
        )


def _walk_path_item(
    before_spec: dict[str, Any],
    after_spec: dict[str, Any],
    path: str,
    before_item: dict[str, Any],
    after_item: dict[str, Any],
) -> Iterator[RawChange]:
    before_ops = _operations(before_item)
    after_ops = _operations(after_item)
    for method in sorted(set(after_ops) - set(before_ops)):
        yield RawChange(
            kind="openapi.operation.added",
            location=f"/paths/{path}/{method}",
            before=None,
            after=after_ops[method],
            detail={"method": method.upper(), "path": path},
        )
    for method in sorted(set(before_ops) - set(after_ops)):
        yield RawChange(
            kind="openapi.operation.removed",
            location=f"/paths/{path}/{method}",
            before=before_ops[method],
            after=None,
            detail={"method": method.upper(), "path": path},
        )
    for method in sorted(set(before_ops) & set(after_ops)):
        yield from _walk_operation(
            before_spec,
            after_spec,
            path,
            method,
            before_ops[method],
            after_ops[method],
        )


def _walk_operation(
    before_spec: dict[str, Any],
    after_spec: dict[str, Any],
    path: str,
    method: str,
    before_op: dict[str, Any],
    after_op: dict[str, Any],
) -> Iterator[RawChange]:
    op_loc = f"/paths/{path}/{method}"
    # Parameters.
    before_params = _params_by_key(before_op)
    after_params = _params_by_key(after_op)
    for key in sorted(set(after_params) - set(before_params)):
        in_, name = key
        param = after_params[key]
        required = bool(param.get("required", False))
        kind = (
            "openapi.parameter.added.required" if required else "openapi.parameter.added.optional"
        )
        yield RawChange(
            kind=kind,
            location=f"{op_loc}/parameters/{in_}/{name}",
            before=None,
            after=param,
            detail={
                "in": in_,
                "name": name,
                "required": required,
                "method": method.upper(),
                "path": path,
            },
        )
    for key in sorted(set(before_params) - set(after_params)):
        in_, name = key
        param = before_params[key]
        yield RawChange(
            kind="openapi.parameter.removed",
            location=f"{op_loc}/parameters/{in_}/{name}",
            before=param,
            after=None,
            detail={"in": in_, "name": name, "method": method.upper(), "path": path},
        )
    for key in sorted(set(before_params) & set(after_params)):
        in_, name = key
        before_p = before_params[key]
        after_p = after_params[key]
        was_required = bool(before_p.get("required", False))
        is_required = bool(after_p.get("required", False))
        if was_required != is_required:
            kind = (
                "openapi.parameter.required.increased"
                if is_required
                else "openapi.parameter.required.decreased"
            )
            yield RawChange(
                kind=kind,
                location=f"{op_loc}/parameters/{in_}/{name}",
                before=was_required,
                after=is_required,
                detail={"in": in_, "name": name, "method": method.upper(), "path": path},
            )
        before_type = _param_type(before_p)
        after_type = _param_type(after_p)
        if before_type != after_type and (before_type is not None or after_type is not None):
            yield RawChange(
                kind="openapi.parameter.type_changed",
                location=f"{op_loc}/parameters/{in_}/{name}",
                before=before_type,
                after=after_type,
                detail={"in": in_, "name": name, "method": method.upper(), "path": path},
            )

    # Request body.
    before_body_required = _request_body_required(before_op)
    after_body_required = _request_body_required(after_op)
    before_body = _request_body_schema(before_spec, before_op)
    after_body = _request_body_schema(after_spec, after_op)
    # Treat the request body's required-ness as a wrapping required flag on
    # all its properties for diff classification — practically, what matters
    # for client compatibility is the per-property requireds, which the
    # schema diff already handles.
    if before_body or after_body:
        yield from _diff_schema(
            f"{op_loc}/requestBody",
            before_body,
            after_body,
            side="request_body",
        )
    if before_body_required != after_body_required:
        kind = (
            "openapi.request_body.property.required.increased"
            if after_body_required
            else "openapi.request_body.property.required.decreased"
        )
        yield RawChange(
            kind=kind,
            location=f"{op_loc}/requestBody",
            before=before_body_required,
            after=after_body_required,
            detail={"method": method.upper(), "path": path},
        )

    # Responses.
    before_responses = _as_dict(before_op.get("responses"))
    after_responses = _as_dict(after_op.get("responses"))
    for code in sorted(set(after_responses) - set(before_responses)):
        yield RawChange(
            kind="openapi.response.added",
            location=f"{op_loc}/responses/{code}",
            before=None,
            after=after_responses[code],
            detail={"status": str(code), "method": method.upper(), "path": path},
        )
    for code in sorted(set(before_responses) - set(after_responses)):
        yield RawChange(
            kind="openapi.response.removed",
            location=f"{op_loc}/responses/{code}",
            before=before_responses[code],
            after=None,
            detail={"status": str(code), "method": method.upper(), "path": path},
        )
    for code in sorted(set(before_responses) & set(after_responses)):
        before_schema = _response_schema(before_spec, before_op, str(code))
        after_schema = _response_schema(after_spec, after_op, str(code))
        if before_schema or after_schema:
            yield from _diff_schema(
                f"{op_loc}/responses/{code}",
                before_schema,
                after_schema,
                side="response",
            )


def _walk_components(
    before_spec: dict[str, Any], after_spec: dict[str, Any]
) -> Iterator[RawChange]:
    """Compare component schemas not referenced by request/response.

    For schemas referenced inline, the operation walker already covers
    them via :func:`_diff_schema`. For shared schemas only referenced by
    name, we still want to detect added/removed enum values and
    properties — those propagate to every operation using the schema.
    """
    before = _as_dict(_as_dict(before_spec.get("components")).get("schemas"))
    after = _as_dict(_as_dict(after_spec.get("components")).get("schemas"))
    for name in sorted(set(before) & set(after)):
        before_schema = _as_dict(before[name])
        after_schema = _as_dict(after[name])
        before_enum = before_schema.get("enum")
        after_enum = after_schema.get("enum")
        if isinstance(before_enum, list) and isinstance(after_enum, list):
            before_set = {repr(x) for x in before_enum}
            after_set = {repr(x) for x in after_enum}
            for value in sorted(after_set - before_set):
                yield RawChange(
                    kind="openapi.enum.value.added",
                    location=f"/components/schemas/{name}/enum",
                    before=None,
                    after=value,
                    detail={"schema": name},
                )
            for value in sorted(before_set - after_set):
                yield RawChange(
                    kind="openapi.enum.value.removed",
                    location=f"/components/schemas/{name}/enum",
                    before=value,
                    after=None,
                    detail={"schema": name},
                )
