"""Tests for the ``guardian diff`` CLI used by the GitHub Action.

Acceptance criterion (from the milestone):
    "Action exits non-zero on breaking diff fixture PR" — verified
    here by invoking the CLI on the breaking fixture pair and
    asserting a non-zero exit code, the GitHub-flavoured workflow
    annotations, and a Markdown summary that surfaces per-client
    impact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from guardian_core.db import get_sessionmaker
from guardian_core.models import InferredEndpoint
from typer.testing import CliRunner

from apps.cli.main import app as cli_app

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "diff"
BASE = FIXTURES / "openapi.base.json"
HEAD_BREAKING = FIXTURES / "openapi.head_breaking.json"
HEAD_ADDITIVE = FIXTURES / "openapi.head_additive.json"


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.timeout(10)
def test_breaking_diff_exits_non_zero(runner: CliRunner, migrated_db: str) -> None:
    """The pytest-verifiable acceptance criterion.

    Removing the ``/users`` path is a breaking change per the default
    ruleset; the CLI must exit with a non-zero code so the composite
    GitHub Action propagates failure to the workflow.

    The ``@pytest.mark.timeout(10)`` guard is defensive: if a future
    refactor accidentally introduces a retry loop or a hung subprocess
    in the diff path, this test must fail loudly rather than hang the
    CI run. The assertion below uses ``!= 0`` directly so any non-zero
    exit code (the documented ``2`` or otherwise) satisfies the
    "Action exits non-zero on breaking diff fixture PR" criterion.
    """
    result = runner.invoke(
        cli_app,
        [
            "diff",
            "--base",
            str(BASE),
            "--head",
            str(HEAD_BREAKING),
            "--kind",
            "openapi",
            "--format",
            "json",
        ],
    )
    assert result.exit_code != 0, (
        f"expected non-zero exit on breaking diff; got {result.exit_code}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # The CLI contract is exit code 2 specifically for breaking changes.
    assert result.exit_code == 2, result.exit_code
    payload = json.loads(result.stdout.strip())
    assert payload["summary"]["breaking"] >= 1
    assert any(c["verdict"] == "breaking" for c in payload["changes"])


def test_breaking_diff_with_accept_breaking_exits_zero(runner: CliRunner, migrated_db: str) -> None:
    """``--accept-breaking`` (the bypass label path) suppresses the failure."""
    result = runner.invoke(
        cli_app,
        [
            "diff",
            "--base",
            str(BASE),
            "--head",
            str(HEAD_BREAKING),
            "--format",
            "json",
            "--accept-breaking",
        ],
    )
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["summary"]["breaking"] >= 1


def test_additive_diff_exits_zero(runner: CliRunner, migrated_db: str) -> None:
    """A purely-additive diff must never fail the gate."""
    result = runner.invoke(
        cli_app,
        [
            "diff",
            "--base",
            str(BASE),
            "--head",
            str(HEAD_ADDITIVE),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["summary"]["breaking"] == 0
    assert payload["summary"]["additive"] >= 1


def test_github_format_emits_annotations_and_summary(
    runner: CliRunner, tmp_path: Path, migrated_db: str
) -> None:
    """``--format github`` emits workflow commands to stdout and writes Markdown."""
    summary_path = tmp_path / "summary.md"
    json_path = tmp_path / "report.json"
    result = runner.invoke(
        cli_app,
        [
            "diff",
            "--base",
            str(BASE),
            "--head",
            str(HEAD_BREAKING),
            "--format",
            "github",
            "--summary-out",
            str(summary_path),
            "--json-out",
            str(json_path),
            "--accept-breaking",  # we only want to observe the output here
        ],
    )
    assert result.exit_code == 0, result.stderr
    # Workflow commands are emitted to stdout, one per change.
    assert "::error " in result.stdout or "::error::" in result.stdout
    # The Markdown summary is written to the summary file.
    md = summary_path.read_text()
    assert "## :shield: Guardian" in md
    assert "Per-client impact" in md
    assert "Breaking changes" in md
    # The persisted JSON matches what stdout (in JSON mode) would print.
    payload = json.loads(json_path.read_text())
    assert payload["summary"]["breaking"] >= 1


def test_per_client_impact_lists_affected_repos(
    runner: CliRunner, tmp_path: Path, migrated_db: str
) -> None:
    """The Markdown summary names the mined client repos when present."""
    # Seed an InferredEndpoint that joins onto the removed path.
    sessionmaker = get_sessionmaker()
    with sessionmaker() as s:
        s.add(
            InferredEndpoint(
                repo="acme/users-client",
                commit_sha="deadbeef",
                file="src/api.py",
                line=10,
                language="python",
                client_library="requests",
                method="GET",
                path_template="/users",
                fields={"query": [], "body": []},
                content_hash="a" * 64,
            )
        )
        s.commit()

    summary_path = tmp_path / "summary.md"
    result = runner.invoke(
        cli_app,
        [
            "diff",
            "--base",
            str(BASE),
            "--head",
            str(HEAD_BREAKING),
            "--format",
            "github",
            "--summary-out",
            str(summary_path),
            "--accept-breaking",
        ],
    )
    assert result.exit_code == 0, result.stderr
    md = summary_path.read_text()
    assert "acme/users-client" in md, f"expected client repo in summary:\n{md}"


def test_unknown_kind_is_rejected(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        cli_app,
        [
            "diff",
            "--base",
            str(BASE),
            "--head",
            str(HEAD_BREAKING),
            "--kind",
            "bogus",
        ],
    )
    assert result.exit_code != 0


def test_missing_file_is_rejected(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        cli_app,
        [
            "diff",
            "--base",
            str(tmp_path / "nope.json"),
            "--head",
            str(HEAD_BREAKING),
        ],
    )
    assert result.exit_code != 0
