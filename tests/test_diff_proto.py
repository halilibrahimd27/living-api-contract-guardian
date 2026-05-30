"""Protobuf descriptor-diff fixture matrix."""

from __future__ import annotations

from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    FileDescriptorSet,
    MethodDescriptorProto,
    ServiceDescriptorProto,
)
from guardian_diff import diff_contracts
from guardian_diff.models import ChangeReport


def _fds(*files: FileDescriptorProto) -> bytes:
    fds = FileDescriptorSet()
    fds.file.extend(files)
    return fds.SerializeToString()


def _file(
    name: str = "users.proto",
    package: str = "users",
    messages: list[DescriptorProto] | None = None,
    services: list[ServiceDescriptorProto] | None = None,
) -> FileDescriptorProto:
    f = FileDescriptorProto()
    f.name = name
    f.package = package
    if messages:
        f.message_type.extend(messages)
    if services:
        f.service.extend(services)
    return f


def _msg(name: str, fields: list[FieldDescriptorProto] | None = None) -> DescriptorProto:
    m = DescriptorProto()
    m.name = name
    if fields:
        m.field.extend(fields)
    return m


def _field(
    name: str,
    number: int,
    type_: int = FieldDescriptorProto.TYPE_STRING,
    label: int = FieldDescriptorProto.LABEL_OPTIONAL,
    type_name: str = "",
) -> FieldDescriptorProto:
    f = FieldDescriptorProto()
    f.name = name
    f.number = number
    f.type = type_
    f.label = label
    if type_name:
        f.type_name = type_name
    return f


def _service(name: str, methods: list[MethodDescriptorProto]) -> ServiceDescriptorProto:
    s = ServiceDescriptorProto()
    s.name = name
    s.method.extend(methods)
    return s


def _method(name: str, input_type: str, output_type: str) -> MethodDescriptorProto:
    m = MethodDescriptorProto()
    m.name = name
    m.input_type = input_type
    m.output_type = output_type
    return m


def _by_kind(r: ChangeReport, kind: str) -> list[object]:
    return [c for c in r.changes if c.kind == kind]


def _run(before: bytes, after: bytes) -> ChangeReport:
    return diff_contracts(kind="proto", before=before, after=after)


# ---------- messages ----------


def test_message_added_is_additive() -> None:
    before = _fds(_file(messages=[_msg("User", [_field("id", 1)])]))
    after = _fds(
        _file(messages=[_msg("User", [_field("id", 1)]), _msg("Group", [_field("name", 1)])])
    )
    report = _run(before, after)
    hits = _by_kind(report, "proto.message.added")
    assert len(hits) == 1 and hits[0].verdict == "additive"


def test_message_removed_is_breaking() -> None:
    before = _fds(
        _file(messages=[_msg("User", [_field("id", 1)]), _msg("Group", [_field("name", 1)])])
    )
    after = _fds(_file(messages=[_msg("User", [_field("id", 1)])]))
    report = _run(before, after)
    hits = _by_kind(report, "proto.message.removed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


# ---------- fields ----------


def test_field_added_is_additive() -> None:
    before = _fds(_file(messages=[_msg("User", [_field("id", 1)])]))
    after = _fds(_file(messages=[_msg("User", [_field("id", 1), _field("email", 2)])]))
    report = _run(before, after)
    hits = _by_kind(report, "proto.field.added")
    assert len(hits) == 1 and hits[0].verdict == "additive"


def test_field_removed_is_breaking() -> None:
    before = _fds(_file(messages=[_msg("User", [_field("id", 1), _field("legacy", 2)])]))
    after = _fds(_file(messages=[_msg("User", [_field("id", 1)])]))
    report = _run(before, after)
    hits = _by_kind(report, "proto.field.removed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_field_number_changed_is_breaking() -> None:
    before = _fds(_file(messages=[_msg("User", [_field("id", 1)])]))
    after = _fds(_file(messages=[_msg("User", [_field("id", 2)])]))
    report = _run(before, after)
    hits = _by_kind(report, "proto.field.number_changed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"
    # The walker must not emit spurious added/removed events for the rename.
    assert _by_kind(report, "proto.field.added") == []
    assert _by_kind(report, "proto.field.removed") == []


def test_field_type_changed_is_breaking() -> None:
    before = _fds(
        _file(messages=[_msg("User", [_field("id", 1, type_=FieldDescriptorProto.TYPE_STRING)])])
    )
    after = _fds(
        _file(messages=[_msg("User", [_field("id", 1, type_=FieldDescriptorProto.TYPE_INT32)])])
    )
    report = _run(before, after)
    hits = _by_kind(report, "proto.field.type_changed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_field_label_changed_is_breaking() -> None:
    before = _fds(
        _file(
            messages=[_msg("User", [_field("ids", 1, label=FieldDescriptorProto.LABEL_OPTIONAL)])]
        )
    )
    after = _fds(
        _file(
            messages=[_msg("User", [_field("ids", 1, label=FieldDescriptorProto.LABEL_REPEATED)])]
        )
    )
    report = _run(before, after)
    hits = _by_kind(report, "proto.field.label_changed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_field_renamed_is_behavioral() -> None:
    before = _fds(_file(messages=[_msg("User", [_field("name", 1)])]))
    after = _fds(_file(messages=[_msg("User", [_field("display_name", 1)])]))
    report = _run(before, after)
    hits = _by_kind(report, "proto.field.renamed")
    assert len(hits) == 1 and hits[0].verdict == "behavioral"


# ---------- services / RPCs ----------


def test_service_added_is_additive() -> None:
    before = _fds(_file(messages=[_msg("U", [_field("id", 1)])]))
    after = _fds(
        _file(
            messages=[_msg("U", [_field("id", 1)])],
            services=[_service("UserSvc", [_method("Get", ".users.U", ".users.U")])],
        )
    )
    report = _run(before, after)
    hits = _by_kind(report, "proto.service.added")
    assert len(hits) == 1 and hits[0].verdict == "additive"


def test_rpc_removed_is_breaking() -> None:
    svc_before = _service(
        "UserSvc",
        [
            _method("Get", ".users.U", ".users.U"),
            _method("Drop", ".users.U", ".users.U"),
        ],
    )
    svc_after = _service("UserSvc", [_method("Get", ".users.U", ".users.U")])
    before = _fds(_file(messages=[_msg("U", [_field("id", 1)])], services=[svc_before]))
    after = _fds(_file(messages=[_msg("U", [_field("id", 1)])], services=[svc_after]))
    report = _run(before, after)
    hits = _by_kind(report, "proto.rpc.removed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_rpc_request_type_changed_is_breaking() -> None:
    before_svc = _service("UserSvc", [_method("Get", ".users.U", ".users.U")])
    after_svc = _service("UserSvc", [_method("Get", ".users.U2", ".users.U")])
    msgs = [_msg("U", [_field("id", 1)]), _msg("U2", [_field("id", 1)])]
    before = _fds(_file(messages=msgs, services=[before_svc]))
    after = _fds(_file(messages=msgs, services=[after_svc]))
    report = _run(before, after)
    hits = _by_kind(report, "proto.rpc.request_type_changed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_rpc_response_type_changed_is_breaking() -> None:
    before_svc = _service("UserSvc", [_method("Get", ".users.U", ".users.U")])
    after_svc = _service("UserSvc", [_method("Get", ".users.U", ".users.U2")])
    msgs = [_msg("U", [_field("id", 1)]), _msg("U2", [_field("id", 1)])]
    before = _fds(_file(messages=msgs, services=[before_svc]))
    after = _fds(_file(messages=msgs, services=[after_svc]))
    report = _run(before, after)
    hits = _by_kind(report, "proto.rpc.response_type_changed")
    assert len(hits) == 1 and hits[0].verdict == "breaking"


def test_noop_proto_diff_yields_no_changes() -> None:
    fds = _fds(_file(messages=[_msg("U", [_field("id", 1)])]))
    report = _run(fds, fds)
    assert report.summary.total == 0
