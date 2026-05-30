"""LLM-drafted per-client migration guides.

For each persisted :class:`~guardian_core.models.ContractDiff`, this
package generates a markdown migration guide tailored to one client
repo's mined call sites. Generation is grounded by three explicit
sections in the prompt: (1) the ChangeReport entries affecting the
client, (2) up to N call sites with surrounding source lines mined in
M2, and (3) a language-specific style hint.

Determinism / reproducibility:

* The LLM provider is abstracted as :class:`LLMProvider`; tests inject a
  :class:`MockLLMProvider` keyed by ``hash(prompt)`` so the same prompt
  always returns the same canned completion.
* Guides are cached by ``hash(diff_id, client_id, prompt_version,
  model)`` in the ``guides`` table — a second request with identical
  inputs serves from cache and never calls the LLM.
* Generated code snippets are parsed via tree-sitter (no execution).
  On parse error the service retries with a stricter prompt up to
  ``RETRY_LIMIT`` times before giving up.
"""

from __future__ import annotations

from guardian_guides.llm import LiteLLMProvider, LLMProvider, MockLLMProvider, prompt_hash
from guardian_guides.models import (
    CallSiteContext,
    ChangeSummary,
    GuideContext,
    GuideRequest,
    GuideResult,
)
from guardian_guides.service import (
    PROMPT_VERSION,
    GuideGenerationError,
    GuideService,
    build_cache_key,
)
from guardian_guides.syntax import (
    SnippetParseError,
    extract_code_blocks,
    validate_markdown_snippets,
)

__all__ = [
    "PROMPT_VERSION",
    "CallSiteContext",
    "ChangeSummary",
    "GuideContext",
    "GuideGenerationError",
    "GuideRequest",
    "GuideResult",
    "GuideService",
    "LLMProvider",
    "LiteLLMProvider",
    "MockLLMProvider",
    "SnippetParseError",
    "build_cache_key",
    "extract_code_blocks",
    "prompt_hash",
    "validate_markdown_snippets",
]
