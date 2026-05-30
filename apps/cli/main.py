"""Typer-based CLI for the Guardian platform.

Registered as the ``guardian`` console script via ``pyproject.toml``.
Keeps the command surface minimal at this milestone; future milestones
attach service-management and contract-introspection sub-commands.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer
from guardian_core.db import get_database_url, get_engine, session_scope
from guardian_core.logging import configure_logging, get_logger
from guardian_core.mining import mine_repo, persist_call_sites
from guardian_core.mining.repo_scanner import detect_commit_sha
from guardian_core.redis_client import get_redis_url, ping_redis
from guardian_core.version import get_git_sha, get_version
from guardian_diff import diff_contracts, load_default_rules
from guardian_diff.ci_format import (
    render_markdown,
    render_text,
    render_workflow_commands,
)
from guardian_diff.ruleset import load_rules_from_yaml
from sqlalchemy import text

app = typer.Typer(
    name="guardian",
    help="Living API Contract Guardian — command-line interface.",
    add_completion=False,
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the Guardian distribution version."""
    typer.echo(get_version())


@app.command("health")
def health() -> None:
    """Probe DB and Redis connectivity; emit a JSON health record."""
    configure_logging()
    log = get_logger("cli.health")

    db_ok = False
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # pragma: no cover - exercised in integration
        log.warning("cli.health.db_failed", error=str(exc))

    redis_ok = ping_redis()

    payload: dict[str, str | bool] = {
        "version": get_version(),
        "git_sha": get_git_sha(),
        "database_url": get_database_url(),
        "redis_url": get_redis_url(),
        "db_ok": db_ok,
        "redis_ok": redis_ok,
    }
    typer.echo(json.dumps(payload, sort_keys=True))
    if not (db_ok and redis_ok):
        sys.exit(1)


@app.command("migrate")
def migrate() -> None:
    """Run Alembic ``upgrade head`` against the configured database."""
    configure_logging()
    log = get_logger("cli.migrate")
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_database_url())
    log.info("cli.migrate.start", url=get_database_url())
    command.upgrade(cfg, "head")
    log.info("cli.migrate.done")


@app.command("mine")
def mine(
    repo: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),
    repo_name: str = typer.Option(
        "",
        "--name",
        help="Logical repo name to record (defaults to the path's basename).",
    ),
    commit_sha: str = typer.Option(
        "",
        "--sha",
        help="Commit SHA to record (default: `git rev-parse HEAD` on the repo).",
    ),
) -> None:
    """Statically mine a client repo for HTTP/gRPC call sites."""
    configure_logging()
    log = get_logger("cli.mine")

    name = repo_name or repo.name
    sha = commit_sha or detect_commit_sha(repo)
    log.info("cli.mine.start", repo=name, sha=sha, path=str(repo))
    sites = mine_repo(repo)
    with session_scope() as session:
        result = persist_call_sites(session, repo=name, commit_sha=sha, sites=sites)
    typer.echo(json.dumps(result.model_dump(), sort_keys=True))
    log.info(
        "cli.mine.done",
        repo=name,
        sha=sha,
        inserted=result.inserted,
        skipped=result.skipped,
    )


def _load_spec(source: str, *, kind: str, git_path: str | None) -> Any:
    """Resolve ``source`` to a spec value matching the ``diff_contracts`` API.

    ``source`` is one of:

    * A local file path (if ``git_path`` is ``None``).
    * A git ref (commit SHA, branch, tag) plus a separate ``git_path``,
      in which case ``git show <ref>:<git_path>`` materialises the spec.

    For ``kind="openapi"`` the file is parsed as JSON and returned as a
    dict. For ``kind="proto"`` the file is read as raw bytes (an
    already-serialised ``FileDescriptorSet`` produced by
    ``protoc --descriptor_set_out``).
    """
    if git_path is not None:
        try:
            blob = subprocess.check_output(
                ["git", "show", f"{source}:{git_path}"],
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:  # pragma: no cover - env-dependent
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise typer.BadParameter(
                f"git show {source}:{git_path} failed: {stderr.strip() or exc}"
            ) from exc
    else:
        path = Path(source)
        if not path.exists():
            raise typer.BadParameter(f"spec path does not exist: {source}")
        blob = path.read_bytes()

    if kind == "openapi":
        try:
            return json.loads(blob)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"failed to parse {source} as JSON: {exc}") from exc
    # proto: raw bytes (FileDescriptorSet)
    return blob


@app.command("diff")
def diff(
    base: str = typer.Option(
        ...,
        "--base",
        help="Path to the BEFORE spec (or a git ref when --spec is given).",
    ),
    head: str = typer.Option(
        ...,
        "--head",
        help="Path to the AFTER spec (or a git ref when --spec is given).",
    ),
    spec_path: str | None = typer.Option(
        None,
        "--spec",
        help=(
            "Spec path inside the repo. When set, --base and --head are "
            "treated as git refs and the spec is loaded via `git show <ref>:<spec>`."
        ),
    ),
    kind: str = typer.Option(
        "openapi",
        "--kind",
        case_sensitive=False,
        help="Contract kind: openapi | proto.",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        case_sensitive=False,
        help="Output format: text | json | github.",
    ),
    ruleset_path: Path | None = typer.Option(
        None,
        "--ruleset",
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="Optional YAML ruleset to merge over the shipped defaults.",
    ),
    accept_breaking: bool = typer.Option(
        False,
        "--accept-breaking/--no-accept-breaking",
        help=(
            "If true, return exit code 0 even when breaking changes are "
            "detected (used by the GitHub Action when the bypass label is set)."
        ),
    ),
    summary_path: Path | None = typer.Option(
        None,
        "--summary-out",
        help=(
            "If set, also write the Markdown summary to this path "
            "(used to populate $GITHUB_STEP_SUMMARY)."
        ),
    ),
    json_out: Path | None = typer.Option(
        None,
        "--json-out",
        help="If set, also persist the full ChangeReport JSON to this path.",
    ),
) -> None:
    """Diff two contract specs and emit a ChangeReport.

    Exit code is non-zero (``2``) if breaking changes are detected and
    ``--accept-breaking`` is not set; otherwise ``0``. This is the
    interface the composite GitHub Action shells out to.
    """
    configure_logging()
    log = get_logger("cli.diff")

    kind_lower = kind.lower()
    if kind_lower not in {"openapi", "proto"}:
        raise typer.BadParameter("--kind must be 'openapi' or 'proto'")
    fmt_lower = output_format.lower()
    if fmt_lower not in {"text", "json", "github"}:
        raise typer.BadParameter("--format must be 'text', 'json' or 'github'")

    before = _load_spec(base, kind=kind_lower, git_path=spec_path)
    after = _load_spec(head, kind=kind_lower, git_path=spec_path)

    ruleset = load_rules_from_yaml(ruleset_path) if ruleset_path else load_default_rules()

    try:
        with session_scope() as session:
            report = diff_contracts(
                kind=kind_lower,  # type: ignore[arg-type]
                before=before,
                after=after,
                ruleset=ruleset,
                session=session,
            )
    except Exception as exc:  # pragma: no cover - exercised in integration
        # Fall back to a no-session run if the DB is unreachable so the
        # CI gate still works on fresh runners without a Guardian backend.
        log.warning("cli.diff.no_session", error=str(exc))
        report = diff_contracts(
            kind=kind_lower,  # type: ignore[arg-type]
            before=before,
            after=after,
            ruleset=ruleset,
            session=None,
        )

    log.info(
        "cli.diff.summary",
        kind=kind_lower,
        total=report.summary.total,
        breaking=report.summary.breaking,
        behavioral=report.summary.behavioral,
        additive=report.summary.additive,
    )

    if fmt_lower == "json":
        typer.echo(json.dumps(report.model_dump(mode="json"), sort_keys=True))
    elif fmt_lower == "github":
        # The GitHub Action reads workflow commands from stdout and a
        # Markdown summary from the file pointed to by --summary-out.
        typer.echo(render_workflow_commands(report))
        markdown = render_markdown(report)
        if summary_path is None:
            typer.echo(markdown)
        else:
            summary_path.write_text(markdown)
    else:  # text
        typer.echo(render_text(report))

    if json_out is not None:
        json_out.write_text(json.dumps(report.model_dump(mode="json"), sort_keys=True))

    if report.summary.breaking > 0 and not accept_breaking:
        log.warning(
            "cli.diff.breaking",
            breaking=report.summary.breaking,
            accept_breaking=accept_breaking,
        )
        raise typer.Exit(code=2)


def main() -> None:
    """Entrypoint used by the ``guardian`` console script."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
