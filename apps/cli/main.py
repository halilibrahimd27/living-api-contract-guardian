"""Typer-based CLI for the Guardian platform.

Registered as the ``guardian`` console script via ``pyproject.toml``.
Keeps the command surface minimal at this milestone; future milestones
attach service-management and contract-introspection sub-commands.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from guardian_core.db import get_database_url, get_engine, session_scope
from guardian_core.logging import configure_logging, get_logger
from guardian_core.mining import mine_repo, persist_call_sites
from guardian_core.mining.repo_scanner import detect_commit_sha
from guardian_core.redis_client import get_redis_url, ping_redis
from guardian_core.version import get_git_sha, get_version
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


def main() -> None:
    """Entrypoint used by the ``guardian`` console script."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
