"""Walk a repo, run per-language visitors, and persist results."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from guardian_core.logging import get_logger
from guardian_core.mining.js_visitor import JsVisitor
from guardian_core.mining.models import InferredCallSite, Language
from guardian_core.mining.python_visitor import PythonVisitor
from guardian_core.models import InferredEndpoint

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session


_PY_SUFFIXES: frozenset[str] = frozenset({".py"})
_JS_SUFFIXES: frozenset[str] = frozenset({".js", ".jsx", ".mjs", ".cjs"})
_TS_SUFFIXES: frozenset[str] = frozenset({".ts", ".tsx"})

_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".next",
        ".nuxt",
    }
)


class PersistenceResult(BaseModel):
    """Outcome of writing miner findings to the database."""

    model_config = ConfigDict(extra="forbid")

    repo: str
    commit_sha: str
    inserted: int
    skipped: int
    total: int


def _classify(path: Path) -> Language | None:
    suffix = path.suffix.lower()
    if suffix in _PY_SUFFIXES:
        return "python"
    if suffix in _JS_SUFFIXES:
        return "javascript"
    if suffix in _TS_SUFFIXES:
        return "typescript"
    return None


def _iter_source_files(root: Path) -> list[tuple[Path, Language]]:
    out: list[tuple[Path, Language]] = []
    for path in sorted(root.rglob("*")):
        if any(part in _IGNORE_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        lang = _classify(path)
        if lang is None:
            continue
        out.append((path, lang))
    return out


def detect_commit_sha(root: Path) -> str:
    """Return the HEAD commit SHA of ``root`` if it is a git repo, else 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                return sha
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def mine_repo(root: Path) -> list[InferredCallSite]:
    """Run all visitors over ``root`` and return discovered call sites."""
    log = get_logger("mining.repo")
    results: list[InferredCallSite] = []
    for path, lang in _iter_source_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_bytes()
        except OSError as exc:
            log.warning("mining.read_failed", path=str(path), error=str(exc))
            continue
        try:
            if lang == "python":
                visitor = PythonVisitor(rel, source)
                results.extend(visitor.visit())
            else:
                js_visitor = JsVisitor(rel, source, lang)
                results.extend(js_visitor.visit())
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("mining.parse_failed", path=str(path), language=lang, error=str(exc))
    return results


def persist_call_sites(
    session: Session,
    *,
    repo: str,
    commit_sha: str,
    sites: list[InferredCallSite],
) -> PersistenceResult:
    """Upsert ``sites`` into ``inferred_endpoints``.

    The unique key is ``(repo, commit_sha, content_hash)``, so re-running
    the miner on the same SHA is a no-op for already-seen rows. We hash
    each site individually and skip rows whose hash is already present.
    """
    if not sites:
        return PersistenceResult(repo=repo, commit_sha=commit_sha, inserted=0, skipped=0, total=0)

    existing = {
        row[0]
        for row in session.query(InferredEndpoint.content_hash)
        .filter(InferredEndpoint.repo == repo, InferredEndpoint.commit_sha == commit_sha)
        .all()
    }

    inserted = 0
    skipped = 0
    seen_in_batch: set[str] = set()
    for site in sites:
        digest = site.content_hash()
        if digest in existing or digest in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(digest)
        row = InferredEndpoint(
            repo=repo,
            commit_sha=commit_sha,
            file=site.file,
            line=site.line,
            language=site.language,
            client_library=site.client_library,
            method=site.method.upper(),
            path_template=site.path_template,
            fields={"names": site.fields},
            content_hash=digest,
        )
        session.add(row)
        inserted += 1
    session.flush()
    return PersistenceResult(
        repo=repo,
        commit_sha=commit_sha,
        inserted=inserted,
        skipped=skipped,
        total=inserted + skipped,
    )
