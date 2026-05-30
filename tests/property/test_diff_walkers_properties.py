"""Property-based tests for OpenAPI and Protobuf diff walkers.

Invariants tested:
1. diff_openapi with identical specs produces no changes
2. diff_openapi changes all have kind and location fields
3. diff_openapi always terminates (no infinite loops)
4. diff_proto with identical descriptor sets produces no changes
5. diff_proto changes all have valid proto change kinds
6. Paths in OpenAPI changes are correctly formatted
7. Locations in proto changes include message/service names
"""

from __future__ import annotations

from typing import Any

from google.protobuf.descriptor_pb2 import FileDescriptorSet
from guardian_diff.openapi import diff_openapi
from guardian_diff.proto import diff_proto
from hypothesis import assume, given
from hypothesis import strategies as st


def _empty_openapi_spec() -> dict[str, Any]:
    """Generate a minimal valid OpenAPI 3.x spec."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {},
    }


def _openapi_spec_with_path(path: str) -> dict[str, Any]:
    """Generate an OpenAPI spec with a single GET endpoint."""
    spec = _empty_openapi_spec()
    spec["paths"][path] = {"get": {"responses": {"200": {"description": "OK"}}}}
    return spec


def _openapi_spec_with_parameter(
    path: str, param_name: str, required: bool = False
) -> dict[str, Any]:
    """Generate an OpenAPI spec with a path that has a parameter."""
    spec = _empty_openapi_spec()
    spec["paths"][path] = {
        "get": {
            "parameters": [
                {
                    "name": param_name,
                    "in": "query",
                    "schema": {"type": "string"},
                    "required": required,
                }
            ],
            "responses": {"200": {"description": "OK"}},
        }
    }
    return spec


def _minimal_proto_descriptor_set() -> bytes:
    """Generate a minimal valid FileDescriptorSet."""
    fds = FileDescriptorSet()
    file_proto = fds.file.add()
    file_proto.name = "test.proto"
    file_proto.package = "test"
    return fds.SerializeToString()


def _proto_descriptor_set_with_message(message_name: str) -> bytes:
    """Generate a FileDescriptorSet with a single message type."""
    fds = FileDescriptorSet()
    file_proto = fds.file.add()
    file_proto.name = "test.proto"
    file_proto.package = "test"
    msg = file_proto.message_type.add()
    msg.name = message_name
    return fds.SerializeToString()


def _proto_descriptor_set_with_field(
    message_name: str, field_name: str, field_number: int
) -> bytes:
    """Generate a FileDescriptorSet with a message containing a field."""
    fds = FileDescriptorSet()
    file_proto = fds.file.add()
    file_proto.name = "test.proto"
    file_proto.package = "test"
    msg = file_proto.message_type.add()
    msg.name = message_name
    field = msg.field.add()
    field.name = field_name
    field.number = field_number
    field.type = 9  # TYPE_STRING
    return fds.SerializeToString()


class TestDiffOpenAPIIdentical:
    """Property tests for diff_openapi with identical specs."""

    @given(st.just(_empty_openapi_spec()))
    def test_identical_empty_specs_no_changes(self, spec: dict[str, Any]) -> None:
        """Diffing two identical empty OpenAPI specs produces no changes."""
        changes = diff_openapi(spec, spec)
        assert len(changes) == 0

    @given(st.text(min_size=1, max_size=50, alphabet="/abcdefghijklmnopqrstuvwxyz0123456789_-"))
    def test_identical_specs_with_path_no_changes(self, path: str) -> None:
        """Diffing identical specs with same paths produces no changes."""
        assume("/" in path or path.startswith("/"))
        spec = _openapi_spec_with_path(path)
        changes = diff_openapi(spec, spec)
        assert len(changes) == 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_identical_specs_with_parameters_no_changes(self, path: str, param: str) -> None:
        """Diffing identical specs with same parameters produces no changes."""
        spec = _openapi_spec_with_parameter(f"/{path}", param)
        changes = diff_openapi(spec, spec)
        assert len(changes) == 0


class TestDiffOpenAPIStructure:
    """Property tests for diff_openapi output structure."""

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_all_changes_have_kind(self, path: str) -> None:
        """All changes returned by diff_openapi have a kind field."""
        before = _empty_openapi_spec()
        after = _openapi_spec_with_path(f"/{path}")
        changes = diff_openapi(before, after)
        for change in changes:
            assert isinstance(change.kind, str)
            assert len(change.kind) > 0
            assert change.kind.startswith("openapi.")

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_all_changes_have_location(self, path: str) -> None:
        """All changes returned by diff_openapi have a location field."""
        before = _empty_openapi_spec()
        after = _openapi_spec_with_path(f"/{path}")
        changes = diff_openapi(before, after)
        for change in changes:
            assert isinstance(change.location, str)
            assert len(change.location) > 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_path_added_location_includes_path(self, path: str) -> None:
        """path.added changes include the path in the location."""
        before = _empty_openapi_spec()
        after = _openapi_spec_with_path(f"/{path}")
        changes = diff_openapi(before, after)
        path_added = [c for c in changes if c.kind == "openapi.path.added"]
        assert len(path_added) > 0
        for change in path_added:
            assert f"/{path}" in change.location or path in change.location

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_path_added_detail_has_methods(self, path: str) -> None:
        """path.added changes include methods in detail."""
        before = _empty_openapi_spec()
        after = _openapi_spec_with_path(f"/{path}")
        changes = diff_openapi(before, after)
        path_added = [c for c in changes if c.kind == "openapi.path.added"]
        assert len(path_added) > 0
        for change in path_added:
            assert "methods" in change.detail
            assert isinstance(change.detail["methods"], list)

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_parameter_changes_include_detail(self, path: str) -> None:
        """Parameter changes include in/name in detail."""
        before = _openapi_spec_with_parameter(f"/{path}", "old_param")
        after = _openapi_spec_with_parameter(f"/{path}", "new_param")
        changes = diff_openapi(before, after)
        param_changes = [c for c in changes if c.kind.startswith("openapi.parameter.")]
        for change in param_changes:
            assert "in" in change.detail or "name" in change.detail


class TestDiffOpenAPIPathAddedRemoved:
    """Property tests for path.added and path.removed changes."""

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_new_path_emits_path_added(self, path: str) -> None:
        """Adding a path emits openapi.path.added."""
        before = _empty_openapi_spec()
        after = _openapi_spec_with_path(f"/{path}")
        changes = diff_openapi(before, after)
        path_added = [c for c in changes if c.kind == "openapi.path.added"]
        assert len(path_added) > 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_removed_path_emits_path_removed(self, path: str) -> None:
        """Removing a path emits openapi.path.removed."""
        before = _openapi_spec_with_path(f"/{path}")
        after = _empty_openapi_spec()
        changes = diff_openapi(before, after)
        path_removed = [c for c in changes if c.kind == "openapi.path.removed"]
        assert len(path_removed) > 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_path_added_before_is_none(self, path: str) -> None:
        """path.added changes have before=None."""
        before = _empty_openapi_spec()
        after = _openapi_spec_with_path(f"/{path}")
        changes = diff_openapi(before, after)
        path_added = [c for c in changes if c.kind == "openapi.path.added"]
        for change in path_added:
            assert change.before is None

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_path_removed_after_is_none(self, path: str) -> None:
        """path.removed changes have after=None."""
        before = _openapi_spec_with_path(f"/{path}")
        after = _empty_openapi_spec()
        changes = diff_openapi(before, after)
        path_removed = [c for c in changes if c.kind == "openapi.path.removed"]
        for change in path_removed:
            assert change.after is None


class TestDiffProtoIdentical:
    """Property tests for diff_proto with identical descriptor sets."""

    def test_identical_minimal_proto_no_changes(self) -> None:
        """Diffing two identical minimal proto descriptor sets produces no changes."""
        fds = _minimal_proto_descriptor_set()
        changes = diff_proto(fds, fds)
        assert len(changes) == 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_identical_proto_with_message_no_changes(self, message: str) -> None:
        """Diffing identical protos with same message produces no changes."""
        fds = _proto_descriptor_set_with_message(message)
        changes = diff_proto(fds, fds)
        assert len(changes) == 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.integers(min_value=1, max_value=1000),
    )
    def test_identical_proto_with_field_no_changes(
        self, message: str, field: str, number: int
    ) -> None:
        """Diffing identical protos with same field produces no changes."""
        fds = _proto_descriptor_set_with_field(message, field, number)
        changes = diff_proto(fds, fds)
        assert len(changes) == 0


class TestDiffProtoStructure:
    """Property tests for diff_proto output structure."""

    def test_all_changes_have_kind(self) -> None:
        """All changes returned by diff_proto have a kind field."""
        before = _minimal_proto_descriptor_set()
        after = _proto_descriptor_set_with_message("TestMessage")
        changes = diff_proto(before, after)
        for change in changes:
            assert isinstance(change.kind, str)
            assert len(change.kind) > 0
            assert change.kind.startswith("proto.")

    def test_all_changes_have_location(self) -> None:
        """All changes returned by diff_proto have a location field."""
        before = _minimal_proto_descriptor_set()
        after = _proto_descriptor_set_with_message("TestMessage")
        changes = diff_proto(before, after)
        for change in changes:
            assert isinstance(change.location, str)
            assert len(change.location) > 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_message_added_location_includes_message_name(self, message: str) -> None:
        """proto.message.added changes include the message name in location."""
        before = _minimal_proto_descriptor_set()
        after = _proto_descriptor_set_with_message(message)
        changes = diff_proto(before, after)
        msg_added = [c for c in changes if c.kind == "proto.message.added"]
        assert len(msg_added) > 0
        for change in msg_added:
            assert message in change.location

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_message_added_detail_has_message_name(self, message: str) -> None:
        """proto.message.added changes include message name in detail."""
        before = _minimal_proto_descriptor_set()
        after = _proto_descriptor_set_with_message(message)
        changes = diff_proto(before, after)
        msg_added = [c for c in changes if c.kind == "proto.message.added"]
        assert len(msg_added) > 0
        for change in msg_added:
            assert "message" in change.detail


class TestDiffProtoMessageAddedRemoved:
    """Property tests for proto.message.added and proto.message.removed."""

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_new_message_emits_message_added(self, message: str) -> None:
        """Adding a message emits proto.message.added."""
        before = _minimal_proto_descriptor_set()
        after = _proto_descriptor_set_with_message(message)
        changes = diff_proto(before, after)
        msg_added = [c for c in changes if c.kind == "proto.message.added"]
        assert len(msg_added) > 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_removed_message_emits_message_removed(self, message: str) -> None:
        """Removing a message emits proto.message.removed."""
        before = _proto_descriptor_set_with_message(message)
        after = _minimal_proto_descriptor_set()
        changes = diff_proto(before, after)
        msg_removed = [c for c in changes if c.kind == "proto.message.removed"]
        assert len(msg_removed) > 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_message_added_before_is_none(self, message: str) -> None:
        """proto.message.added changes have before=None."""
        before = _minimal_proto_descriptor_set()
        after = _proto_descriptor_set_with_message(message)
        changes = diff_proto(before, after)
        msg_added = [c for c in changes if c.kind == "proto.message.added"]
        for change in msg_added:
            assert change.before is None

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    def test_message_removed_after_is_none(self, message: str) -> None:
        """proto.message.removed changes have after=None."""
        before = _proto_descriptor_set_with_message(message)
        after = _minimal_proto_descriptor_set()
        changes = diff_proto(before, after)
        msg_removed = [c for c in changes if c.kind == "proto.message.removed"]
        for change in msg_removed:
            assert change.after is None


class TestDiffProtoFieldAddedRemoved:
    """Property tests for proto.field.added and proto.field.removed."""

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.integers(min_value=1, max_value=1000),
    )
    def test_new_field_emits_field_added(self, message: str, field: str, number: int) -> None:
        """Adding a field emits proto.field.added."""
        before = _proto_descriptor_set_with_message(message)
        after = _proto_descriptor_set_with_field(message, field, number)
        changes = diff_proto(before, after)
        field_added = [c for c in changes if c.kind == "proto.field.added"]
        assert len(field_added) > 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.integers(min_value=1, max_value=1000),
    )
    def test_removed_field_emits_field_removed(self, message: str, field: str, number: int) -> None:
        """Removing a field emits proto.field.removed."""
        before = _proto_descriptor_set_with_field(message, field, number)
        after = _proto_descriptor_set_with_message(message)
        changes = diff_proto(before, after)
        field_removed = [c for c in changes if c.kind == "proto.field.removed"]
        assert len(field_removed) > 0

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.integers(min_value=1, max_value=1000),
    )
    def test_field_added_before_is_none(self, message: str, field: str, number: int) -> None:
        """proto.field.added changes have before=None."""
        before = _proto_descriptor_set_with_message(message)
        after = _proto_descriptor_set_with_field(message, field, number)
        changes = diff_proto(before, after)
        field_added = [c for c in changes if c.kind == "proto.field.added"]
        for change in field_added:
            assert change.before is None

    @given(
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"),
        st.integers(min_value=1, max_value=1000),
    )
    def test_field_removed_after_is_none(self, message: str, field: str, number: int) -> None:
        """proto.field.removed changes have after=None."""
        before = _proto_descriptor_set_with_field(message, field, number)
        after = _proto_descriptor_set_with_message(message)
        changes = diff_proto(before, after)
        field_removed = [c for c in changes if c.kind == "proto.field.removed"]
        for change in field_removed:
            assert change.after is None
