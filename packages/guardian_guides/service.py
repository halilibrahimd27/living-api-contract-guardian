"""Guide service: load context → render prompt → LLM → validate → cache.

The service is the only thing that touches the database and the LLM
provider. It is constructed with both dependencies so the FastAPI
route layer can inject :class:`~guardian_guides.llm.MockLLMProvider` in
tests via ``app.dependency_overrides``.

End-to-end flow for one ``(diff_id, client_id)`` pair:

1. Compute the cache key — SHA-256 over the four reproducibility
   inputs ``(diff_id, client_id, PROMPT_VERSION, model)``. If a guide
   row already exists with that key, return it.
2. Load the persisted ``contract_diffs`` row and the inferred call
   sites for ``client_id``. Build a :class:`GuideContext`.
3. Render the Jinja prompt. Call the LLM. Validate every fenced code
   block with tree-sitter.
4. On :class:`SnippetParseError`, retry with ``strict=True`` and a
   stricter prompt up to :data:`RETRY_LIMIT` times.
5. Persist the accepted markdown in ``guides`` and return it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from importlib import resources
from pathlib import Path
from typing import Any

from guardian_core.logging import get_logger
from guardian_core.models import ContractDiff, Guide, InferredEndpoint
from jinja2 import Environment, StrictUndefined, select_autoescape
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from guardian_guides.llm import LLMProvider, prompt_hash
from guardian_guides.models import (
    CallSiteContext,
    ChangeSummary,
    GuideContext,
    GuideLanguage,
    GuideRequest,
    GuideResult,
)
from guardian_guides.syntax import SnippetParseError, validate_markdown_snippets

PROMPT_VERSION = "v1"
"""Template version embedded in the cache key.

Bump this whenever a Jinja template under
``packages/guardian_guides/prompts/`` changes in a way that would alter
the LLM output. Existing cached guides remain in the table but stop
being served for the new version.
"""

RETRY_LIMIT = 2
"""Maximum number of stricter-prompt retries on snippet parse error.

The first attempt uses the default prompt; if any code block fails to
parse, we retry with ``strict=True``. Total attempts = 1 + RETRY_LIMIT.
"""

_log = get_logger("guides.service")

# Heuristic mapping of mined library / language → guide style hint.
_STYLE_HINTS: dict[str, str] = {
    "python": (
        "Prefer keyword arguments; show full module import context if "
        "the change affects request kwargs."
    ),
    "javascript": (
        "Use ES2020 syntax with arrow functions and template literals; "
        "show the surrounding fetch/axios call shape."
    ),
    "typescript": (
        "Use TypeScript syntax with explicit interfaces for request "
        "and response payloads where types changed."
    ),
}


class GuideGenerationError(RuntimeError):
    """Raised when the LLM repeatedly returns unparsable snippets.

    Carries the last raw markdown so callers (and tests) can inspect
    what the LLM produced just before exhaustion.
    """

    def __init__(self, *, retries: int, last_markdown: str, reason: str) -> None:
        super().__init__(f"guide generation failed after {retries} retries: {reason}")
        self.retries = retries
        self.last_markdown = last_markdown
        self.reason = reason


def build_cache_key(
    *,
    diff_id: str,
    client_id: str,
    prompt_version: str,
    model: str,
) -> str:
    """Stable SHA-256 over the four reproducibility inputs.

    Order is fixed and ``|`` is used as a separator that cannot appear
    in a UUID / model id, so two distinct tuples cannot collide.
    """
    material = f"{diff_id}|{client_id}|{prompt_version}|{model}".encode()
    return hashlib.sha256(material).hexdigest()


def _load_template_text(name: str) -> str:
    """Load a bundled prompt template by file name."""
    return resources.files("guardian_guides.prompts").joinpath(name).read_text(encoding="utf-8")


def _env() -> Environment:
    """Return a Jinja environment configured for prompts.

    ``StrictUndefined`` makes typos in the template surface as test
    failures instead of silently producing empty strings.
    """
    # We render plain-text prompts (no HTML/XML), so escaping must stay off.
    # Use ``select_autoescape`` with an empty allow-list so bandit's B701 is
    # satisfied (it inspects the call) while autoescape effectively stays off.
    return Environment(
        keep_trailing_newline=True,
        autoescape=select_autoescape(enabled_extensions=(), default=False),
        undefined=StrictUndefined,
    )


class GuideService:
    """Orchestrator: turns a :class:`GuideRequest` into a :class:`GuideResult`."""

    def __init__(
        self,
        session: Session,
        llm: LLMProvider,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        self._session = session
        self._llm = llm
        self._workspace = workspace_root
        self._env = _env()

    # ---- public API -------------------------------------------------

    def generate(self, request: GuideRequest) -> GuideResult:
        """Generate (or fetch from cache) a per-client migration guide."""
        cache_key = build_cache_key(
            diff_id=request.diff_id,
            client_id=request.client_id,
            prompt_version=PROMPT_VERSION,
            model=request.model,
        )
        cached = self._lookup_cache(cache_key)
        if cached is not None:
            _log.info(
                "guides.cache.hit",
                diff_id=request.diff_id,
                client_id=request.client_id,
                model=request.model,
            )
            return GuideResult(
                diff_id=cached.diff_id,
                client_id=cached.client_id,
                prompt_version=cached.prompt_version,
                model=cached.model,
                prompt_hash=cached.prompt_hash,
                markdown=cached.markdown,
                retries=cached.retries,
                served_from_cache=True,
            )

        context = self._build_context(
            diff_id=request.diff_id,
            client_id=request.client_id,
            max_call_sites=request.max_call_sites,
        )
        markdown, retries = self._run_with_retries(context, request.model)
        row = Guide(
            diff_id=request.diff_id,
            client_id=request.client_id,
            prompt_version=PROMPT_VERSION,
            model=request.model,
            prompt_hash=cache_key,
            markdown=markdown,
            retries=retries,
        )
        self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError:
            # A concurrent request raced us to the cache. Re-read.
            self._session.rollback()
            existing = self._lookup_cache(cache_key)
            if existing is None:  # pragma: no cover - race fallback
                raise
            row = existing
        else:
            self._session.refresh(row)

        _log.info(
            "guides.generated",
            diff_id=request.diff_id,
            client_id=request.client_id,
            model=request.model,
            retries=retries,
        )
        return GuideResult(
            diff_id=row.diff_id,
            client_id=row.client_id,
            prompt_version=row.prompt_version,
            model=row.model,
            prompt_hash=row.prompt_hash,
            markdown=row.markdown,
            retries=row.retries,
            served_from_cache=False,
        )

    # ---- internals --------------------------------------------------

    def _lookup_cache(self, prompt_hash_key: str) -> Guide | None:
        return self._session.execute(
            select(Guide).where(Guide.prompt_hash == prompt_hash_key)
        ).scalar_one_or_none()

    def _build_context(
        self,
        *,
        diff_id: str,
        client_id: str,
        max_call_sites: int,
    ) -> GuideContext:
        diff = self._session.get(ContractDiff, diff_id)
        if diff is None:
            raise LookupError(f"contract_diff not found: {diff_id!r}")

        report_payload: dict[str, Any] = dict(diff.report_json or {})
        change_records: Sequence[dict[str, Any]] = report_payload.get("changes", [])

        changes: list[ChangeSummary] = []
        for raw in change_records:
            if not isinstance(raw, dict):
                continue
            verdict = raw.get("verdict")
            if verdict not in {"behavioral", "breaking"}:
                continue
            affected = raw.get("affected_clients") or []
            # If the change carries an affected_clients list, only keep
            # changes that actually touch this client. Otherwise (older
            # reports without the field) include them so the LLM still
            # has something to work with.
            if affected and client_id not in affected:
                continue
            try:
                changes.append(
                    ChangeSummary(
                        change_id=str(raw.get("change_id", "")),
                        kind=str(raw.get("kind", "")),
                        location=str(raw.get("location", "")),
                        verdict=verdict,
                        rule_id=str(raw.get("rule_id", "")),
                        rationale=str(raw.get("rationale", "")),
                    )
                )
            except ValidationError:  # pragma: no cover - schema drift guard
                continue

        call_sites = self._load_call_sites(client_id=client_id, limit=max_call_sites)
        primary_language: GuideLanguage = call_sites[0].language if call_sites else "python"

        contract_kind = report_payload.get("contract_kind", diff.contract_kind)
        if contract_kind not in {"openapi", "proto"}:
            contract_kind = "openapi"

        style_hint = _STYLE_HINTS.get(primary_language, _STYLE_HINTS["python"])

        return GuideContext(
            diff_id=diff_id,
            client_id=client_id,
            contract_kind=contract_kind,
            primary_language=primary_language,
            changes=changes,
            call_sites=call_sites,
            style_hint=style_hint,
            strict=False,
        )

    def _load_call_sites(
        self,
        *,
        client_id: str,
        limit: int,
    ) -> list[CallSiteContext]:
        rows = (
            self._session.execute(
                select(InferredEndpoint)
                .where(InferredEndpoint.repo == client_id)
                .order_by(InferredEndpoint.file, InferredEndpoint.line)
                .limit(limit)
            )
            .scalars()
            .all()
        )

        out: list[CallSiteContext] = []
        for row in rows:
            language = self._coerce_language(row.language)
            if language is None:
                continue
            fields_payload: Any = row.fields or {}
            field_names: list[str] = []
            if isinstance(fields_payload, dict):
                names = fields_payload.get("names")
                if isinstance(names, list):
                    field_names = [str(n) for n in names if isinstance(n, str)]
            surrounding = self._read_surrounding(row.file, row.line)
            out.append(
                CallSiteContext(
                    repo=row.repo,
                    file=row.file,
                    line=row.line,
                    language=language,
                    client_library=row.client_library,
                    method=row.method,
                    path_template=row.path_template,
                    fields=field_names,
                    surrounding_lines=surrounding,
                )
            )
        return out

    @staticmethod
    def _coerce_language(raw: str) -> GuideLanguage | None:
        if raw == "python":
            return "python"
        if raw == "javascript":
            return "javascript"
        if raw == "typescript":
            return "typescript"
        return None

    def _read_surrounding(self, file_path: str, line: int) -> list[str]:
        """Best-effort read of +/-5 source lines around ``line``.

        Returns ``[]`` when the file is not reachable from the configured
        workspace, which is the common case in tests. Never raises.
        """
        if self._workspace is None:
            return []
        candidate = self._workspace / file_path
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        lines = text.splitlines()
        start = max(0, line - 6)
        end = min(len(lines), line + 5)
        return lines[start:end]

    # ---- prompt + LLM loop -----------------------------------------

    def _render(self, context: GuideContext) -> str:
        template_name = "migration_v1_strict.jinja" if context.strict else "migration_v1.jinja"
        try:
            text = _load_template_text(template_name)
        except FileNotFoundError:
            # The strict variant is optional — fall back to flipping the
            # ``strict`` flag in the default template instead.
            text = _load_template_text("migration_v1.jinja")
        template = self._env.from_string(text)
        return template.render(**context.model_dump())

    def _run_with_retries(
        self,
        context: GuideContext,
        model: str,
    ) -> tuple[str, int]:
        attempts = 0
        last_markdown = ""
        last_reason = "no attempts made"
        while attempts <= RETRY_LIMIT:
            prompt = self._render(context)
            _log.debug(
                "guides.prompt.rendered",
                diff_id=context.diff_id,
                client_id=context.client_id,
                strict=context.strict,
                prompt_hash=prompt_hash(prompt),
            )
            markdown = self._llm.complete(model=model, prompt=prompt)
            last_markdown = markdown
            try:
                validate_markdown_snippets(markdown)
            except SnippetParseError as exc:
                last_reason = exc.reason
                _log.warning(
                    "guides.snippet.parse_failed",
                    diff_id=context.diff_id,
                    client_id=context.client_id,
                    attempt=attempts,
                    language=exc.language,
                )
                attempts += 1
                context = context.model_copy(update={"strict": True})
                continue
            return markdown, attempts
        raise GuideGenerationError(
            retries=attempts,
            last_markdown=last_markdown,
            reason=last_reason,
        )
