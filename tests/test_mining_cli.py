"""Tests for `guardian mine <repo>` CLI command."""

from __future__ import annotations

import json
from pathlib import Path

from guardian_core import db as guardian_db
from guardian_core.models import InferredEndpoint
from sqlalchemy import select
from typer.testing import CliRunner

from apps.cli.main import app

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "clients"


def _invoke_mine(repo: Path, *, repo_name: str, sha: str) -> dict[str, object]:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["mine", str(repo), "--name", repo_name, "--sha", sha],
    )
    assert result.exit_code == 0, result.output
    payload: dict[str, object] = json.loads(result.output.strip().splitlines()[-1])
    return payload


def test_mine_writes_rows(migrated_db: str) -> None:
    payload = _invoke_mine(
        FIXTURE_ROOT / "python_requests",
        repo_name="acme/users-client",
        sha="abc1234",
    )
    assert payload["repo"] == "acme/users-client"
    assert payload["commit_sha"] == "abc1234"
    assert isinstance(payload["inserted"], int) and payload["inserted"] > 0

    factory = guardian_db.get_sessionmaker()
    with factory() as session:
        rows = (
            session.execute(
                select(InferredEndpoint).where(
                    InferredEndpoint.repo == "acme/users-client",
                    InferredEndpoint.commit_sha == "abc1234",
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == payload["inserted"]
    methods = {(row.method, row.path_template) for row in rows}
    assert ("GET", "/users/{user_id}") in methods
    assert ("POST", "/users") in methods


def test_mine_idempotent_re_run(migrated_db: str) -> None:
    first = _invoke_mine(
        FIXTURE_ROOT / "js_axios",
        repo_name="acme/customers-web",
        sha="deadbee",
    )
    second = _invoke_mine(
        FIXTURE_ROOT / "js_axios",
        repo_name="acme/customers-web",
        sha="deadbee",
    )
    assert first["inserted"] > 0
    assert second["inserted"] == 0
    assert second["skipped"] == first["inserted"]

    factory = guardian_db.get_sessionmaker()
    with factory() as session:
        count = (
            session.query(InferredEndpoint)
            .filter(
                InferredEndpoint.repo == "acme/customers-web",
                InferredEndpoint.commit_sha == "deadbee",
            )
            .count()
        )
    assert count == first["inserted"]


def test_mine_stores_language_and_library(migrated_db: str) -> None:
    _invoke_mine(
        FIXTURE_ROOT / "python_grpc",
        repo_name="acme/inv-grpc",
        sha="0000001",
    )
    factory = guardian_db.get_sessionmaker()
    with factory() as session:
        rows = (
            session.query(InferredEndpoint).filter(InferredEndpoint.repo == "acme/inv-grpc").all()
        )
    assert rows
    for row in rows:
        assert row.language == "python"
        assert row.client_library == "grpc"
        assert row.method == "RPC"
        assert row.path_template.startswith("/inventory.Inventory/")
        # `fields` is the JSON-serialized {"names": [...]} envelope.
        assert "names" in row.fields
