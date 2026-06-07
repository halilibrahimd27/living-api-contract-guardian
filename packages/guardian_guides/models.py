"""Pydantic v2 models for the migration-guide pipeline."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

GuideLanguage = Literal["python", "javascript", "typescript"]

# Identifiers that must not be silently accepted as empty / whitespace-only.
# Stripping is conservative: a diff_id of ``"  "`` is operator error, not
# a valid cache key, and a client_id of ``""`` would collapse the per-client
# cache with the URL-routed value. Both are rejected at the Pydantic boundary
# so the same constraint holds whether the request comes from the HTTP route
# or from a direct ``GuideService.generate()`` call.
_NonEmptyId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]


class CallSiteContext(BaseModel):
    """A single mined call site enriched with up to N surrounding source lines.

    ``surrounding_lines`` is optional: the miner stores file + line but
    not source text, so the guide service tries to read +/- 5 lines
    around ``line`` from a configured workspace and falls back to an
    empty list when the file cannot be located. The LLM prompt is
    designed to work in either case.
    """

    model_config = ConfigDict(extra="forbid")

    repo: Annotated[str, Field(min_length=1, max_length=512)]
    file: Annotated[str, Field(min_length=1, max_length=1024)]
    line: Annotated[int, Field(ge=1)]
    language: GuideLanguage
    client_library: Annotated[str, Field(min_length=1, max_length=64)]
    method: Annotated[str, Field(min_length=1, max_length=32)]
    path_template: Annotated[str, Field(min_length=1, max_length=1024)]
    fields: list[str] = Field(default_factory=list)
    surrounding_lines: list[str] = Field(default_factory=list)


class ChangeSummary(BaseModel):
    """A condensed ChangeRecord used by the prompt template.

    The full :class:`~guardian_diff.models.ChangeRecord` carries
    raw before/after JSON that we *do not* want to embed verbatim in the
    prompt (it bloats tokens and confuses snippet extraction). This is
    the slim projection used for grounding.
    """

    model_config = ConfigDict(extra="forbid")

    change_id: str
    kind: str
    location: str
    verdict: Literal["additive", "behavioral", "breaking"]
    rule_id: str
    rationale: str


class GuideContext(BaseModel):
    """All grounding data passed to the prompt template.

    A :class:`GuideContext` is rendered into a Jinja prompt: the
    template walks ``changes`` and ``call_sites`` and embeds the
    ``style_hint`` block tailored to ``primary_language``.
    """

    model_config = ConfigDict(extra="forbid")

    diff_id: str
    client_id: str
    contract_kind: Literal["openapi", "proto"]
    primary_language: GuideLanguage
    changes: list[ChangeSummary]
    call_sites: list[CallSiteContext]
    style_hint: str
    strict: bool = False


class GuideRequest(BaseModel):
    """A single call into :meth:`GuideService.generate`."""

    model_config = ConfigDict(extra="forbid")

    diff_id: _NonEmptyId
    client_id: _NonEmptyId
    model: Annotated[str, Field(min_length=1, max_length=128)] = "gpt-4o-mini"
    max_call_sites: Annotated[int, Field(ge=1, le=50)] = 10


class GuideResult(BaseModel):
    """The output of :meth:`GuideService.generate`."""

    model_config = ConfigDict(extra="forbid")

    diff_id: str
    client_id: str
    prompt_version: str
    model: str
    prompt_hash: str
    markdown: str
    retries: int = 0
    served_from_cache: bool = False
