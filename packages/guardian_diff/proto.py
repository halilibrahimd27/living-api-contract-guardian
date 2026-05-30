"""Protobuf descriptor-set raw-change walker.

Given two serialised :class:`FileDescriptorSet` byte blobs (as produced
by ``protoc --descriptor_set_out``), parse them into descriptor objects
and emit a :class:`~guardian_diff.models.RawChange` for each structural
delta. The canonical "breaking" axes per the protobuf wire format are:

* field number changes (same field name, new tag — re-binds the data)
* field type changes (tag number now decodes to a different type)
* field label changes (optional ↔ required ↔ repeated)
* field removals
* RPC request / response type changes
"""

from __future__ import annotations

from collections.abc import Iterator

from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    EnumDescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    FileDescriptorSet,
    ServiceDescriptorProto,
)

from guardian_diff.models import RawChange


def _parse(blob: bytes) -> FileDescriptorSet:
    fds = FileDescriptorSet()
    fds.ParseFromString(blob)
    return fds


def _message_index(file: FileDescriptorProto, prefix: str) -> dict[str, DescriptorProto]:
    out: dict[str, DescriptorProto] = {}
    for msg in file.message_type:
        full = f"{prefix}.{msg.name}" if prefix else msg.name
        out[full] = msg
        # nested
        out.update(_message_index_nested(msg, full))
    return out


def _message_index_nested(msg: DescriptorProto, prefix: str) -> dict[str, DescriptorProto]:
    out: dict[str, DescriptorProto] = {}
    for nested in msg.nested_type:
        full = f"{prefix}.{nested.name}"
        out[full] = nested
        out.update(_message_index_nested(nested, full))
    return out


def _all_messages(fds: FileDescriptorSet) -> dict[str, DescriptorProto]:
    out: dict[str, DescriptorProto] = {}
    for file in fds.file:
        out.update(_message_index(file, file.package))
    return out


def _all_enums(fds: FileDescriptorSet) -> dict[str, EnumDescriptorProto]:
    out: dict[str, EnumDescriptorProto] = {}
    for file in fds.file:
        for enum in file.enum_type:
            full = f"{file.package}.{enum.name}" if file.package else enum.name
            out[full] = enum
    return out


def _all_services(
    fds: FileDescriptorSet,
) -> dict[str, ServiceDescriptorProto]:
    out: dict[str, ServiceDescriptorProto] = {}
    for file in fds.file:
        for service in file.service:
            full = f"{file.package}.{service.name}" if file.package else service.name
            out[full] = service
    return out


def _field_type_label(field: FieldDescriptorProto) -> str:
    """Render a field's type as a stable, comparable string."""
    if field.type == FieldDescriptorProto.TYPE_MESSAGE:
        return f"message:{field.type_name}"
    if field.type == FieldDescriptorProto.TYPE_ENUM:
        return f"enum:{field.type_name}"
    # FieldDescriptorProto.Type enum values are stable across protobuf versions.
    return f"scalar:{FieldDescriptorProto.Type.Name(field.type)}"


def _field_label(field: FieldDescriptorProto) -> str:
    return str(FieldDescriptorProto.Label.Name(field.label))


def _fields_by_number(msg: DescriptorProto) -> dict[int, FieldDescriptorProto]:
    return {f.number: f for f in msg.field}


def _fields_by_name(msg: DescriptorProto) -> dict[str, FieldDescriptorProto]:
    return {f.name: f for f in msg.field}


def _diff_message(
    name: str, before: DescriptorProto, after: DescriptorProto
) -> Iterator[RawChange]:
    before_by_num = _fields_by_number(before)
    after_by_num = _fields_by_number(after)
    before_by_name = _fields_by_name(before)
    after_by_name = _fields_by_name(after)

    # New fields: present in `after` but their number is not in `before`.
    for num in sorted(set(after_by_num) - set(before_by_num)):
        f = after_by_num[num]
        # If the same name existed before with a different number → number change.
        if f.name in before_by_name and before_by_name[f.name].number != num:
            old = before_by_name[f.name]
            yield RawChange(
                kind="proto.field.number_changed",
                location=f"/{name}/{f.name}",
                before=old.number,
                after=num,
                detail={"message": name, "field": f.name},
            )
            continue
        yield RawChange(
            kind="proto.field.added",
            location=f"/{name}/{f.name}",
            before=None,
            after={"number": num, "name": f.name, "type": _field_type_label(f)},
            detail={"message": name, "field": f.name, "number": num},
        )

    # Removed fields by number; if the same name appears at a *different*
    # new number, the number-changed event was already emitted above.
    for num in sorted(set(before_by_num) - set(after_by_num)):
        f = before_by_num[num]
        if f.name in after_by_name and after_by_name[f.name].number != num:
            # Handled as proto.field.number_changed in the loop above.
            continue
        yield RawChange(
            kind="proto.field.removed",
            location=f"/{name}/{f.name}",
            before={"number": num, "name": f.name, "type": _field_type_label(f)},
            after=None,
            detail={"message": name, "field": f.name, "number": num},
        )

    # Fields kept at the same tag number: check type / label / rename.
    for num in sorted(set(before_by_num) & set(after_by_num)):
        bf = before_by_num[num]
        af = after_by_num[num]
        if _field_type_label(bf) != _field_type_label(af):
            yield RawChange(
                kind="proto.field.type_changed",
                location=f"/{name}/{bf.name}",
                before=_field_type_label(bf),
                after=_field_type_label(af),
                detail={"message": name, "field": bf.name, "number": num},
            )
        if _field_label(bf) != _field_label(af):
            yield RawChange(
                kind="proto.field.label_changed",
                location=f"/{name}/{bf.name}",
                before=_field_label(bf),
                after=_field_label(af),
                detail={"message": name, "field": bf.name, "number": num},
            )
        if bf.name != af.name:
            yield RawChange(
                kind="proto.field.renamed",
                location=f"/{name}/{bf.name}",
                before=bf.name,
                after=af.name,
                detail={"message": name, "number": num},
            )


def _diff_service(
    name: str, before: ServiceDescriptorProto, after: ServiceDescriptorProto
) -> Iterator[RawChange]:
    before_methods = {m.name: m for m in before.method}
    after_methods = {m.name: m for m in after.method}
    for method_name in sorted(set(after_methods) - set(before_methods)):
        m = after_methods[method_name]
        yield RawChange(
            kind="proto.rpc.added",
            location=f"/{name}/{method_name}",
            before=None,
            after={
                "name": method_name,
                "input_type": m.input_type,
                "output_type": m.output_type,
            },
            detail={"service": name, "method": method_name},
        )
    for method_name in sorted(set(before_methods) - set(after_methods)):
        m = before_methods[method_name]
        yield RawChange(
            kind="proto.rpc.removed",
            location=f"/{name}/{method_name}",
            before={
                "name": method_name,
                "input_type": m.input_type,
                "output_type": m.output_type,
            },
            after=None,
            detail={"service": name, "method": method_name},
        )
    for method_name in sorted(set(before_methods) & set(after_methods)):
        bm = before_methods[method_name]
        am = after_methods[method_name]
        if bm.input_type != am.input_type:
            yield RawChange(
                kind="proto.rpc.request_type_changed",
                location=f"/{name}/{method_name}",
                before=bm.input_type,
                after=am.input_type,
                detail={"service": name, "method": method_name},
            )
        if bm.output_type != am.output_type:
            yield RawChange(
                kind="proto.rpc.response_type_changed",
                location=f"/{name}/{method_name}",
                before=bm.output_type,
                after=am.output_type,
                detail={"service": name, "method": method_name},
            )


def diff_proto(before_bytes: bytes, after_bytes: bytes) -> list[RawChange]:
    """Return the raw change set between two serialized FileDescriptorSets."""
    before = _parse(before_bytes)
    after = _parse(after_bytes)
    out: list[RawChange] = []

    # Messages.
    before_msgs = _all_messages(before)
    after_msgs = _all_messages(after)
    for name in sorted(set(after_msgs) - set(before_msgs)):
        out.append(
            RawChange(
                kind="proto.message.added",
                location=f"/messages/{name}",
                before=None,
                after={"name": name},
                detail={"message": name},
            )
        )
    for name in sorted(set(before_msgs) - set(after_msgs)):
        out.append(
            RawChange(
                kind="proto.message.removed",
                location=f"/messages/{name}",
                before={"name": name},
                after=None,
                detail={"message": name},
            )
        )
    for name in sorted(set(before_msgs) & set(after_msgs)):
        out.extend(_diff_message(name, before_msgs[name], after_msgs[name]))

    # Services.
    before_svcs = _all_services(before)
    after_svcs = _all_services(after)
    for name in sorted(set(after_svcs) - set(before_svcs)):
        out.append(
            RawChange(
                kind="proto.service.added",
                location=f"/services/{name}",
                before=None,
                after={"name": name},
                detail={"service": name},
            )
        )
    for name in sorted(set(before_svcs) - set(after_svcs)):
        out.append(
            RawChange(
                kind="proto.service.removed",
                location=f"/services/{name}",
                before={"name": name},
                after=None,
                detail={"service": name},
            )
        )
    for name in sorted(set(before_svcs) & set(after_svcs)):
        out.extend(_diff_service(name, before_svcs[name], after_svcs[name]))

    return out
