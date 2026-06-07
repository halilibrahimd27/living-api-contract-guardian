"""Property-based tests for migration guide generation.

Uses Hypothesis to verify invariants about:
- Cache key generation (deterministic hashing, collision resistance)
- Prompt hashing (stable SHA-256)
- Code block extraction (fence parsing)
- Snippet validation (tree-sitter integration)
- Mock LLM provider (deterministic test doubles)
- Pydantic model validation (GuideRequest, GuideResult, etc.)
"""

from __future__ import annotations

import hashlib

import pytest
from guardian_guides import (
    CallSiteContext,
    ChangeSummary,
    GuideRequest,
    GuideResult,
    MockLLMProvider,
    SnippetParseError,
    build_cache_key,
    extract_code_blocks,
    prompt_hash,
    validate_markdown_snippets,
)
from guardian_guides.syntax import CodeBlock
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError

# ============================================================================
# Strategies for property test inputs
# ============================================================================


@st.composite
def cache_key_inputs(
    draw: st.DrawFn,
) -> tuple[str, str, str, str]:
    """Generate valid cache key inputs: (diff_id, client_id, prompt_version, model)."""
    diff_id = draw(
        st.text(min_size=1, max_size=256, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    client_id = draw(
        st.text(min_size=1, max_size=256, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    prompt_version = draw(
        st.text(min_size=1, max_size=64, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    model = draw(
        st.text(min_size=1, max_size=128, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    return diff_id, client_id, prompt_version, model


@st.composite
def distinct_cache_key_inputs(
    draw: st.DrawFn,
) -> tuple[tuple[str, str, str, str], tuple[str, str, str, str]]:
    """Generate two distinct sets of cache key inputs."""
    first = draw(cache_key_inputs())
    # Ensure the second differs in at least one component
    second = draw(cache_key_inputs())
    assume(first != second)
    return first, second


# Simple text strategy for markdown
simple_text = st.text(
    alphabet=st.characters(exclude_categories=("Cc", "Cs")),
    min_size=0,
    max_size=1000,
)


@st.composite
def markdown_with_fences(draw: st.DrawFn, num_fences: int = 1) -> str:
    """Generate markdown with N fenced code blocks."""
    parts: list[str] = []
    for _ in range(num_fences):
        # Prefix text
        prefix = draw(simple_text)
        parts.append(prefix)

        # Fence with optional language
        lang = draw(
            st.one_of(
                st.just(""),
                st.sampled_from(["python", "javascript", "typescript", "py", "js", "ts"]),
            )
        )
        if lang:
            parts.append(f"```{lang}\n")
        else:
            parts.append("```\n")

        # Body (must not contain backticks to avoid premature fence close).
        # min_size=1 prevents empty-body fences from merging under the regex
        # when multiple fences appear consecutively with no prefix text.
        body = draw(
            st.text(
                alphabet=st.characters(exclude_characters="`", exclude_categories=("Cc", "Cs")),
                min_size=1,
                max_size=200,
            )
        )
        parts.append(body)
        parts.append("\n```\n")

    # Suffix
    suffix = draw(simple_text)
    parts.append(suffix)
    return "".join(parts)


@st.composite
def valid_code_snippets(draw: st.DrawFn) -> tuple[str, str]:
    """Generate (language, valid_code) pairs."""
    language, code = draw(
        st.sampled_from(
            [
                ("python", "x = 1\ny = x + 2"),
                ("python", "def foo():\n    pass"),
                ("javascript", "const x = 1;\nconst y = x + 2;"),
                ("javascript", "function foo() {}"),
                ("typescript", "const x: number = 1;"),
                ("typescript", "interface Point { x: number; y: number; }"),
            ]
        )
    )
    return language, code


@st.composite
def invalid_code_snippets(draw: st.DrawFn) -> tuple[str, str]:
    """Generate (language, invalid_code) pairs that fail tree-sitter parsing."""
    language, code = draw(
        st.sampled_from(
            [
                ("python", "def foo(:\n    pass"),  # Missing closing paren
                ("python", "x = "),  # Incomplete statement
                ("javascript", "const x = {;"),  # Invalid brace
                ("javascript", "function foo(( ) {}"),  # Double paren
                ("typescript", "interface X { x: }"),  # Missing type
            ]
        )
    )
    return language, code


# ============================================================================
# Property tests for cache key generation
# ============================================================================


class TestBuildCacheKey:
    """Invariants about cache key generation."""

    @given(cache_key_inputs())
    def test_returns_hex_digest(
        self,
        inputs: tuple[str, str, str, str],
    ) -> None:
        """Cache key is always a valid 64-char hex (SHA-256) digest."""
        diff_id, client_id, prompt_version, model = inputs
        key = build_cache_key(
            diff_id=diff_id,
            client_id=client_id,
            prompt_version=prompt_version,
            model=model,
        )
        # SHA-256 produces 32 bytes = 64 hex chars
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    @given(cache_key_inputs())
    def test_deterministic(
        self,
        inputs: tuple[str, str, str, str],
    ) -> None:
        """Same inputs always produce same key."""
        diff_id, client_id, prompt_version, model = inputs
        key1 = build_cache_key(
            diff_id=diff_id,
            client_id=client_id,
            prompt_version=prompt_version,
            model=model,
        )
        key2 = build_cache_key(
            diff_id=diff_id,
            client_id=client_id,
            prompt_version=prompt_version,
            model=model,
        )
        assert key1 == key2

    @given(distinct_cache_key_inputs())
    def test_collision_resistance(
        self,
        inputs: tuple[tuple[str, str, str, str], tuple[str, str, str, str]],
    ) -> None:
        """Different inputs (almost always) produce different keys."""
        diff_id1, client_id1, pv1, model1 = inputs[0]
        diff_id2, client_id2, pv2, model2 = inputs[1]

        key1 = build_cache_key(
            diff_id=diff_id1,
            client_id=client_id1,
            prompt_version=pv1,
            model=model1,
        )
        key2 = build_cache_key(
            diff_id=diff_id2,
            client_id=client_id2,
            prompt_version=pv2,
            model=model2,
        )
        # Due to the properties of SHA-256, different inputs almost
        # certainly produce different keys. If a collision occurs, it's
        # a cryptographic failure that is negligibly likely; Hypothesis
        # will catch if our implementation has a logic bug (e.g.,
        # swapped inputs always produce the same key).
        assert key1 != key2

    @given(cache_key_inputs())
    def test_order_matters(
        self,
        inputs: tuple[str, str, str, str],
    ) -> None:
        """Swapping inputs changes the key."""
        diff_id, client_id, prompt_version, model = inputs

        key_original = build_cache_key(
            diff_id=diff_id,
            client_id=client_id,
            prompt_version=prompt_version,
            model=model,
        )

        # Swap diff_id and client_id
        key_swapped = build_cache_key(
            diff_id=client_id,
            client_id=diff_id,
            prompt_version=prompt_version,
            model=model,
        )

        # Order matters unless inputs are identical
        if diff_id != client_id:
            assert key_original != key_swapped

    def test_separator_can_collide_when_inputs_contain_pipe(self) -> None:
        """Known limitation: pipe in inputs can produce the same concatenated string.

        ``build_cache_key("a|b", "c", …)`` and ``build_cache_key("a", "b|c", …)``
        both hash ``"a|b|c|…"`` identically.  This is an accepted trade-off; in
        practice, diff_id is a UUID and model identifiers do not contain ``|``.
        """
        key1 = build_cache_key(
            diff_id="a|b",
            client_id="c",
            prompt_version="d",
            model="e",
        )
        key2 = build_cache_key(
            diff_id="a",
            client_id="b|c",
            prompt_version="d",
            model="e",
        )
        assert key1 == key2


# ============================================================================
# Property tests for prompt hashing
# ============================================================================


class TestPromptHash:
    """Invariants about prompt hash generation."""

    @given(simple_text)
    def test_returns_hex_digest(self, prompt: str) -> None:
        """Prompt hash is always a valid 64-char hex (SHA-256) digest."""
        h = prompt_hash(prompt)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    @given(simple_text)
    def test_deterministic(self, prompt: str) -> None:
        """Same prompt always produces same hash."""
        h1 = prompt_hash(prompt)
        h2 = prompt_hash(prompt)
        assert h1 == h2

    @given(st.text(min_size=1), st.text(min_size=1))
    def test_different_prompts_differ(
        self,
        prompt1: str,
        prompt2: str,
    ) -> None:
        """Different prompts (almost always) produce different hashes."""
        assume(prompt1 != prompt2)
        h1 = prompt_hash(prompt1)
        h2 = prompt_hash(prompt2)
        assert h1 != h2

    def test_empty_prompt_has_hash(self) -> None:
        """Empty prompt still produces a valid hash."""
        h = prompt_hash("")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    @given(simple_text)
    def test_matches_sha256(self, prompt: str) -> None:
        """Hash matches the expected SHA-256 of the prompt bytes."""
        expected = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        actual = prompt_hash(prompt)
        assert actual == expected


# ============================================================================
# Property tests for code block extraction
# ============================================================================


class TestExtractCodeBlocks:
    """Invariants about extracting fenced code blocks from markdown."""

    def test_empty_markdown_yields_no_blocks(self) -> None:
        """Markdown with no fences returns empty list."""
        blocks = extract_code_blocks("")
        assert blocks == []

    @given(simple_text)
    def test_unfenced_markdown_yields_no_blocks(self, text: str) -> None:
        """Markdown without fences returns empty list."""
        assume("```" not in text)
        blocks = extract_code_blocks(text)
        assert blocks == []

    @given(markdown_with_fences(num_fences=1))
    def test_single_fence_yields_one_block(self, markdown: str) -> None:
        """Markdown with one fence yields exactly one CodeBlock."""
        blocks = extract_code_blocks(markdown)
        assert len(blocks) == 1
        assert isinstance(blocks[0], CodeBlock)

    @given(markdown_with_fences(num_fences=3))
    def test_multiple_fences_yield_blocks(self, markdown: str) -> None:
        """Markdown with N fences yields N CodeBlocks."""
        # We explicitly generate 3 fences
        blocks = extract_code_blocks(markdown)
        assert len(blocks) == 3

    @given(markdown_with_fences(num_fences=1))
    def test_block_has_language_field(self, markdown: str) -> None:
        """Each CodeBlock has a language field (possibly empty)."""
        blocks = extract_code_blocks(markdown)
        assert len(blocks) >= 1
        for block in blocks:
            assert hasattr(block, "language")
            assert isinstance(block.language, str)

    @given(markdown_with_fences(num_fences=1))
    def test_block_has_body_field(self, markdown: str) -> None:
        """Each CodeBlock has a body field containing source."""
        blocks = extract_code_blocks(markdown)
        assert len(blocks) >= 1
        for block in blocks:
            assert hasattr(block, "body")
            assert isinstance(block.body, str)

    def test_language_tag_extracted(self) -> None:
        """Language tag in fence is extracted into block.language."""
        markdown = "```python\nx = 1\n```"
        blocks = extract_code_blocks(markdown)
        assert len(blocks) == 1
        assert blocks[0].language == "python"

    def test_no_language_tag_empty_string(self) -> None:
        """Fence without language tag has empty language."""
        markdown = "```\nx = 1\n```"
        blocks = extract_code_blocks(markdown)
        assert len(blocks) == 1
        assert blocks[0].language == ""

    def test_body_contains_source(self) -> None:
        """Block body contains only content between backticks."""
        markdown = "```python\nprint('hello')\nprint('world')\n```"
        blocks = extract_code_blocks(markdown)
        assert len(blocks) == 1
        assert "print('hello')" in blocks[0].body
        assert "print('world')" in blocks[0].body

    def test_language_case_insensitive(self) -> None:
        """Language tags are normalized to lowercase."""
        markdown = "```PYTHON\nx = 1\n```"
        blocks = extract_code_blocks(markdown)
        assert len(blocks) == 1
        assert blocks[0].language == "python"

    @given(
        st.text(
            min_size=1,
            max_size=20,
            alphabet=st.sampled_from(
                list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-")
            ),
        )
    )
    def test_language_whitespace_stripped(self, lang: str) -> None:
        """Trailing whitespace after the language tag is stripped.

        The fence regex matches ``[A-Za-z0-9_+-]+`` immediately after the
        opening backticks, then ``\\s*\\n``, so only *trailing* whitespace is
        valid here — leading spaces before the tag are not recognised.
        """
        markdown = f"```{lang}   \nx = 1\n```"
        blocks = extract_code_blocks(markdown)
        assert len(blocks) == 1
        assert blocks[0].language == lang.strip().lower()


# ============================================================================
# Property tests for snippet validation
# ============================================================================


class TestValidateMarkdownSnippets:
    """Invariants about validating code snippets in markdown."""

    def test_empty_markdown_reports_zeros(self) -> None:
        """Empty markdown produces a report with all zeros."""
        report = validate_markdown_snippets("")
        assert report.total_blocks == 0
        assert report.validated_blocks == 0
        assert report.skipped_blocks == 0

    def test_counts_sum_correctly(self) -> None:
        """validated_blocks + skipped_blocks == total_blocks."""
        markdown = "```python\nx=1\n```\n```unknown\nx=1\n```"
        report = validate_markdown_snippets(markdown)
        assert report.validated_blocks + report.skipped_blocks == report.total_blocks

    def test_untagged_fence_skipped(self) -> None:
        """Fence without language tag is skipped, not validated."""
        markdown = "```\nx = 1\n```"
        report = validate_markdown_snippets(markdown)
        assert report.total_blocks == 1
        assert report.skipped_blocks == 1
        assert report.validated_blocks == 0

    def test_unknown_language_skipped(self) -> None:
        """Fence with unknown language is skipped, not validated."""
        markdown = "```cobol\nx = 1\n```"
        report = validate_markdown_snippets(markdown)
        assert report.total_blocks == 1
        assert report.skipped_blocks == 1
        assert report.validated_blocks == 0

    @given(valid_code_snippets())
    def test_valid_python_javascript_typescript_accepted(
        self,
        snippet: tuple[str, str],
    ) -> None:
        """Valid code in known languages is validated, not skipped."""
        language, code = snippet
        markdown = f"```{language}\n{code}\n```"
        report = validate_markdown_snippets(markdown)
        assert report.total_blocks == 1
        assert report.validated_blocks == 1
        assert report.skipped_blocks == 0

    @given(invalid_code_snippets())
    def test_invalid_syntax_raises_error(
        self,
        snippet: tuple[str, str],
    ) -> None:
        """Invalid syntax in known language raises SnippetParseError."""
        language, code = snippet
        markdown = f"```{language}\n{code}\n```"
        with pytest.raises(SnippetParseError) as exc_info:
            validate_markdown_snippets(markdown)
        assert exc_info.value.language == language
        assert exc_info.value.snippet == code
        assert exc_info.value.reason  # Has a reason

    def test_error_includes_context(self) -> None:
        """SnippetParseError includes language, snippet, and reason."""
        markdown = "```python\nif x (\n```"
        with pytest.raises(SnippetParseError) as exc_info:
            validate_markdown_snippets(markdown)
        error = exc_info.value
        assert error.language == "python"
        assert error.snippet
        assert error.reason

    def test_first_invalid_block_raises_immediately(self) -> None:
        """Validation stops and raises on first parse error."""
        # First block is invalid, second is valid
        markdown = "```python\nif x (\n```\n```python\nx = 1\n```"
        with pytest.raises(SnippetParseError):
            validate_markdown_snippets(markdown)

    @given(valid_code_snippets())
    def test_multiple_valid_blocks_all_validated(
        self,
        snippet: tuple[str, str],
    ) -> None:
        """Multiple valid blocks are all validated."""
        language, code = snippet
        markdown = f"```{language}\n{code}\n```\n```{language}\n{code}\n```"
        report = validate_markdown_snippets(markdown)
        assert report.total_blocks == 2
        assert report.validated_blocks == 2
        assert report.skipped_blocks == 0

    def test_mixed_valid_and_skipped_counts_correctly(self) -> None:
        """Valid and skipped blocks are counted separately."""
        markdown = (
            "```python\nx = 1\n```\n"
            "```\nno lang\n```\n"
            "```javascript\nconst x = 1;\n```\n"
            "```unknown\nx\n```"
        )
        report = validate_markdown_snippets(markdown)
        assert report.total_blocks == 4
        assert report.validated_blocks == 2  # python and javascript
        assert report.skipped_blocks == 2  # empty and unknown


# ============================================================================
# Property tests for MockLLMProvider
# ============================================================================


class TestMockLLMProvider:
    """Invariants about the mock LLM provider."""

    @given(simple_text, simple_text)
    def test_add_and_complete_roundtrip(
        self,
        prompt: str,
        completion: str,
    ) -> None:
        """Adding a completion allows retrieving it by prompt."""
        provider = MockLLMProvider()
        provider.add(prompt, completion)
        result = provider.complete(model="test", prompt=prompt)
        assert result == completion

    @given(simple_text, simple_text)
    def test_add_returns_prompt_hash(
        self,
        prompt: str,
        completion: str,
    ) -> None:
        """MockLLMProvider.add() returns the prompt hash."""
        provider = MockLLMProvider()
        returned_hash = provider.add(prompt, completion)
        assert returned_hash == prompt_hash(prompt)

    @given(simple_text)
    def test_unknown_prompt_raises_key_error(self, prompt: str) -> None:
        """Unknown prompt with no default raises KeyError."""
        provider = MockLLMProvider()
        with pytest.raises(KeyError):
            provider.complete(model="test", prompt=prompt)

    @given(simple_text, simple_text)
    def test_default_returned_for_unknown_prompt(
        self,
        prompt: str,
        default_response: str,
    ) -> None:
        """Unknown prompt returns default if configured."""
        assume(prompt != "known")
        provider = MockLLMProvider(default=default_response)
        result = provider.complete(model="test", prompt=prompt)
        assert result == default_response

    @given(simple_text)
    def test_calls_tracked(self, prompt: str) -> None:
        """Each complete() call is recorded in provider.calls."""
        provider = MockLLMProvider(default="response")
        provider.complete(model="test-model", prompt=prompt)
        assert len(provider.calls) == 1
        model, h, response = provider.calls[0]
        assert model == "test-model"
        assert h == prompt_hash(prompt)
        assert response == "response"

    @given(
        st.lists(
            st.tuples(simple_text, simple_text),
            min_size=1,
            max_size=10,
            unique_by=lambda x: x[0],  # Unique prompts
        )
    )
    def test_multiple_calls_all_tracked(
        self,
        calls: list[tuple[str, str]],
    ) -> None:
        """All calls to complete() are tracked regardless of hit/miss."""
        provider = MockLLMProvider(default="fallback")
        for prompt, _ in calls:
            provider.complete(model="test", prompt=prompt)
        assert len(provider.calls) == len(calls)

    @given(st.text(min_size=1, max_size=128))
    def test_add_by_hash_allows_retrieval(self, hash_key: str) -> None:
        """add_by_hash() registers a completion by hash."""
        provider = MockLLMProvider()
        provider.add_by_hash(hash_key, "response")
        # We can't easily test retrieval without a matching prompt,
        # but we can verify the internal state
        assert hash_key in provider._responses

    @given(simple_text)
    def test_explicit_response_takes_precedence_over_default(
        self,
        prompt: str,
    ) -> None:
        """Explicit response for a prompt takes precedence over default."""
        provider = MockLLMProvider(default="default_response")
        provider.add(prompt, "explicit_response")
        result = provider.complete(model="test", prompt=prompt)
        assert result == "explicit_response"

    @given(
        st.lists(
            st.tuples(simple_text, simple_text),
            min_size=1,
            max_size=5,
            unique_by=lambda x: x[0],
        )
    )
    def test_initialization_with_responses_dict(
        self,
        responses_list: list[tuple[str, str]],
    ) -> None:
        """MockLLMProvider can be initialized with a responses dict."""
        responses = {prompt_hash(prompt): completion for prompt, completion in responses_list}
        provider = MockLLMProvider(responses=responses)
        for prompt, expected_completion in responses_list:
            result = provider.complete(model="test", prompt=prompt)
            assert result == expected_completion


# ============================================================================
# Integration tests for GuideService interaction patterns
# ============================================================================


class TestGuideServiceIntegration:
    """Invariants about GuideService behavior patterns."""

    def test_cache_key_deterministic_across_service_calls(self) -> None:
        """Cache key for same inputs is always identical."""
        diff_id = "diff-123"
        client_id = "client-456"
        prompt_version = "v1"
        model = "gpt-4o-mini"

        key1 = build_cache_key(
            diff_id=diff_id,
            client_id=client_id,
            prompt_version=prompt_version,
            model=model,
        )
        key2 = build_cache_key(
            diff_id=diff_id,
            client_id=client_id,
            prompt_version=prompt_version,
            model=model,
        )
        # This is the foundation of the cache: same inputs = same key
        assert key1 == key2

    @given(
        st.lists(
            st.tuples(simple_text, simple_text),
            min_size=1,
            max_size=10,
            unique_by=lambda x: x[0],
        )
    )
    def test_mock_provider_deterministic_caching(
        self,
        prompts_and_responses: list[tuple[str, str]],
    ) -> None:
        """Mock provider returns same response for same prompt hash."""
        provider = MockLLMProvider()
        for prompt, response in prompts_and_responses:
            provider.add(prompt, response)

        # First round of calls
        first_results = [
            provider.complete(model="test", prompt=prompt) for prompt, _ in prompts_and_responses
        ]

        # Second round of calls with fresh provider (same responses)
        provider2 = MockLLMProvider()
        for prompt, response in prompts_and_responses:
            provider2.add(prompt, response)

        second_results = [
            provider2.complete(model="test", prompt=prompt) for prompt, _ in prompts_and_responses
        ]

        # Results should be identical (deterministic)
        assert first_results == second_results
        assert first_results == [response for _, response in prompts_and_responses]

    def test_snippet_validation_report_invariant(self) -> None:
        """SnippetReport always satisfies: total = validated + skipped."""
        test_cases = [
            "",
            "no fences here",
            "```python\nx = 1\n```",
            "```unknown\nx\n```",
            "```\nno lang\n```",
            "```python\nx = 1\n```\n```javascript\nconst y = 1;\n```",
        ]
        for markdown in test_cases:
            try:
                report = validate_markdown_snippets(markdown)
                assert (
                    report.validated_blocks + report.skipped_blocks == report.total_blocks
                ), f"Invariant violated for: {markdown!r}"
            except SnippetParseError:
                # Validation errors are acceptable; the invariant only
                # applies to successful reports.
                pass


# ============================================================================
# Property tests for Pydantic models: GuideRequest, GuideResult, etc.
# ============================================================================


# Alphabet for non-empty identifiers. Excludes surrogates and every category
# of whitespace / control characters Pydantic's ``strip_whitespace=True`` (or
# Python's ``str.strip()``) would chew off the ends of the string. Without
# this filter Hypothesis happily generates e.g. ``"\r"`` — a one-char string
# that strips to empty and trips ``min_length=1``, which is exactly the
# behavior we now want for *real* inputs but defeats the "valid input"
# generators below.
_ID_ALPHABET = st.characters(
    exclude_categories=("Cs", "Cc", "Zs", "Zl", "Zp"),
    exclude_characters=" \t\n\r\f\v",
)


@st.composite
def guide_requests(draw: st.DrawFn) -> tuple[str, str, str, int]:
    """Generate valid GuideRequest inputs."""
    diff_id = draw(st.text(min_size=1, max_size=256, alphabet=_ID_ALPHABET))
    client_id = draw(st.text(min_size=1, max_size=256, alphabet=_ID_ALPHABET))
    model = draw(st.text(min_size=1, max_size=128, alphabet=_ID_ALPHABET))
    max_call_sites = draw(st.integers(min_value=1, max_value=50))
    return diff_id, client_id, model, max_call_sites


@st.composite
def guide_results(draw: st.DrawFn) -> tuple[str, str, str, str, str, str, int, bool]:
    """Generate valid GuideResult inputs."""
    diff_id = draw(
        st.text(min_size=1, max_size=256, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    client_id = draw(
        st.text(min_size=1, max_size=256, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    prompt_version = draw(
        st.text(min_size=1, max_size=64, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    model = draw(
        st.text(min_size=1, max_size=128, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    prompt_hash_val = draw(
        st.text(
            min_size=64,
            max_size=64,
            alphabet="0123456789abcdef",
        )
    )
    markdown = draw(simple_text)
    retries = draw(st.integers(min_value=0, max_value=10))
    served_from_cache = draw(st.booleans())
    return (
        diff_id,
        client_id,
        prompt_version,
        model,
        prompt_hash_val,
        markdown,
        retries,
        served_from_cache,
    )


@st.composite
def call_site_contexts(
    draw: st.DrawFn,
) -> tuple[str, str, int, str, str, str, str, list[str], list[str]]:
    """Generate valid CallSiteContext inputs."""
    repo = draw(
        st.text(min_size=1, max_size=512, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    file_path = draw(
        st.text(min_size=1, max_size=1024, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    line = draw(st.integers(min_value=1, max_value=100000))
    language = draw(st.sampled_from(["python", "javascript", "typescript"]))
    client_library = draw(
        st.text(min_size=1, max_size=64, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    method = draw(
        st.text(min_size=1, max_size=32, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    path_template = draw(
        st.text(min_size=1, max_size=1024, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    fields = draw(
        st.lists(
            st.text(min_size=1, max_size=32, alphabet=st.characters(exclude_categories=("Cs",))),
            max_size=5,
        )
    )
    surrounding_lines = draw(
        st.lists(
            st.text(min_size=0, max_size=500, alphabet=st.characters(exclude_categories=("Cs",))),
            max_size=15,
        )
    )
    return (
        repo,
        file_path,
        line,
        language,
        client_library,
        method,
        path_template,
        fields,
        surrounding_lines,
    )


@st.composite
def change_summaries(draw: st.DrawFn) -> tuple[str, str, str, str, str, str]:
    """Generate valid ChangeSummary inputs."""
    change_id = draw(
        st.text(min_size=1, max_size=256, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    kind = draw(
        st.text(min_size=1, max_size=256, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    location = draw(
        st.text(min_size=1, max_size=256, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    verdict = draw(st.sampled_from(["additive", "behavioral", "breaking"]))
    rule_id = draw(
        st.text(min_size=1, max_size=256, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    rationale = draw(
        st.text(min_size=0, max_size=1000, alphabet=st.characters(exclude_categories=("Cs",)))
    )
    return change_id, kind, location, verdict, rule_id, rationale


class TestGuideRequest:
    """Invariants about GuideRequest model."""

    @given(guide_requests())
    def test_model_creation_with_valid_inputs(
        self,
        inputs: tuple[str, str, str, int],
    ) -> None:
        """GuideRequest can be created with valid inputs."""
        diff_id, client_id, model, max_call_sites = inputs
        request = GuideRequest(
            diff_id=diff_id,
            client_id=client_id,
            model=model,
            max_call_sites=max_call_sites,
        )
        assert request.diff_id == diff_id
        assert request.client_id == client_id
        assert request.model == model
        assert request.max_call_sites == max_call_sites

    @given(guide_requests())
    def test_default_model_is_gpt4_mini(
        self,
        inputs: tuple[str, str, str, int],
    ) -> None:
        """GuideRequest.model defaults to gpt-4o-mini when not specified."""
        diff_id, client_id, _, max_call_sites = inputs
        request = GuideRequest(
            diff_id=diff_id,
            client_id=client_id,
            max_call_sites=max_call_sites,
        )
        assert request.model == "gpt-4o-mini"

    @given(guide_requests())
    def test_default_max_call_sites_is_10(
        self,
        inputs: tuple[str, str, str, int],
    ) -> None:
        """GuideRequest.max_call_sites defaults to 10."""
        diff_id, client_id, model, _ = inputs
        request = GuideRequest(
            diff_id=diff_id,
            client_id=client_id,
            model=model,
        )
        assert request.max_call_sites == 10

    # Sample directly from the whitespace family rather than filtering
    # ``st.text()`` — Hypothesis would otherwise throw away ~all generated
    # examples and trip the ``filter_too_much`` health check.
    _WHITESPACE_IDS = st.text(min_size=0, max_size=8, alphabet=st.sampled_from(" \t\n\r\f\v"))

    @given(_WHITESPACE_IDS)
    def test_empty_diff_id_invalid(self, diff_id: str) -> None:
        """GuideRequest rejects empty / whitespace-only diff_id."""
        with pytest.raises(ValidationError):
            GuideRequest(diff_id=diff_id, client_id="c", model="m", max_call_sites=1)

    @given(_WHITESPACE_IDS)
    def test_empty_client_id_invalid(self, client_id: str) -> None:
        """GuideRequest rejects empty / whitespace-only client_id."""
        with pytest.raises(ValidationError):
            GuideRequest(diff_id="d", client_id=client_id, model="m", max_call_sites=1)

    @given(st.integers(max_value=0))
    def test_max_call_sites_zero_or_negative_invalid(self, max_call_sites: int) -> None:
        """GuideRequest rejects max_call_sites <= 0."""
        with pytest.raises(ValidationError):
            GuideRequest(
                diff_id="d",
                client_id="c",
                model="m",
                max_call_sites=max_call_sites,
            )

    @given(st.integers(min_value=51))
    def test_max_call_sites_over_limit_invalid(self, max_call_sites: int) -> None:
        """GuideRequest rejects max_call_sites > 50."""
        with pytest.raises(ValidationError):
            GuideRequest(
                diff_id="d",
                client_id="c",
                model="m",
                max_call_sites=max_call_sites,
            )


class TestGuideResult:
    """Invariants about GuideResult model."""

    @given(guide_results())
    def test_model_creation_with_valid_inputs(
        self,
        inputs: tuple[str, str, str, str, str, str, int, bool],
    ) -> None:
        """GuideResult can be created with valid inputs."""
        diff_id, client_id, prompt_version, model, prompt_hash_val, markdown, retries, cache = (
            inputs
        )
        result = GuideResult(
            diff_id=diff_id,
            client_id=client_id,
            prompt_version=prompt_version,
            model=model,
            prompt_hash=prompt_hash_val,
            markdown=markdown,
            retries=retries,
            served_from_cache=cache,
        )
        assert result.diff_id == diff_id
        assert result.client_id == client_id
        assert result.prompt_version == prompt_version
        assert result.model == model
        assert result.prompt_hash == prompt_hash_val
        assert result.markdown == markdown
        assert result.retries == retries
        assert result.served_from_cache == cache

    @given(guide_results())
    def test_retries_defaults_to_zero(
        self,
        inputs: tuple[str, str, str, str, str, str, int, bool],
    ) -> None:
        """GuideResult.retries defaults to 0."""
        diff_id, client_id, pv, model, ph, markdown, _, cache = inputs
        result = GuideResult(
            diff_id=diff_id,
            client_id=client_id,
            prompt_version=pv,
            model=model,
            prompt_hash=ph,
            markdown=markdown,
            served_from_cache=cache,
        )
        assert result.retries == 0

    @given(guide_results())
    def test_cache_flag_defaults_to_false(
        self,
        inputs: tuple[str, str, str, str, str, str, int, bool],
    ) -> None:
        """GuideResult.served_from_cache defaults to False."""
        diff_id, client_id, pv, model, ph, markdown, retries, _ = inputs
        result = GuideResult(
            diff_id=diff_id,
            client_id=client_id,
            prompt_version=pv,
            model=model,
            prompt_hash=ph,
            markdown=markdown,
            retries=retries,
        )
        assert result.served_from_cache is False

    @given(guide_results())
    def test_roundtrip_via_model_dump(
        self,
        inputs: tuple[str, str, str, str, str, str, int, bool],
    ) -> None:
        """GuideResult roundtrips through model_dump() and model_validate()."""
        diff_id, client_id, pv, model, ph, markdown, retries, cache = inputs
        original = GuideResult(
            diff_id=diff_id,
            client_id=client_id,
            prompt_version=pv,
            model=model,
            prompt_hash=ph,
            markdown=markdown,
            retries=retries,
            served_from_cache=cache,
        )
        dumped = original.model_dump()
        reconstructed = GuideResult.model_validate(dumped)
        assert reconstructed == original


class TestCallSiteContext:
    """Invariants about CallSiteContext model."""

    @given(call_site_contexts())
    def test_model_creation_with_valid_inputs(
        self,
        inputs: tuple[str, str, int, str, str, str, str, list[str], list[str]],
    ) -> None:
        """CallSiteContext can be created with valid inputs."""
        repo, file_path, line, lang, client_lib, method, path_tmpl, fields, surrounding = inputs
        ctx = CallSiteContext(
            repo=repo,
            file=file_path,
            line=line,
            language=lang,
            client_library=client_lib,
            method=method,
            path_template=path_tmpl,
            fields=fields,
            surrounding_lines=surrounding,
        )
        assert ctx.repo == repo
        assert ctx.file == file_path
        assert ctx.line == line
        assert ctx.language == lang
        assert ctx.client_library == client_lib
        assert ctx.method == method
        assert ctx.path_template == path_tmpl
        assert ctx.fields == fields
        assert ctx.surrounding_lines == surrounding

    @given(call_site_contexts())
    def test_fields_defaults_to_empty_list(
        self,
        inputs: tuple[str, str, int, str, str, str, str, list[str], list[str]],
    ) -> None:
        """CallSiteContext.fields defaults to empty list."""
        repo, file_path, line, lang, client_lib, method, path_tmpl, _, surrounding = inputs
        ctx = CallSiteContext(
            repo=repo,
            file=file_path,
            line=line,
            language=lang,
            client_library=client_lib,
            method=method,
            path_template=path_tmpl,
            surrounding_lines=surrounding,
        )
        assert ctx.fields == []

    @given(call_site_contexts())
    def test_surrounding_lines_defaults_to_empty_list(
        self,
        inputs: tuple[str, str, int, str, str, str, str, list[str], list[str]],
    ) -> None:
        """CallSiteContext.surrounding_lines defaults to empty list."""
        repo, file_path, line, lang, client_lib, method, path_tmpl, fields, _ = inputs
        ctx = CallSiteContext(
            repo=repo,
            file=file_path,
            line=line,
            language=lang,
            client_library=client_lib,
            method=method,
            path_template=path_tmpl,
            fields=fields,
        )
        assert ctx.surrounding_lines == []

    @given(st.integers(max_value=0))
    def test_line_zero_or_negative_invalid(self, line: int) -> None:
        """CallSiteContext rejects line <= 0."""
        with pytest.raises(ValidationError):
            CallSiteContext(
                repo="r",
                file="f",
                line=line,
                language="python",
                client_library="cl",
                method="m",
                path_template="pt",
            )

    @given(st.text(max_size=0))
    def test_empty_repo_invalid(self, repo: str) -> None:
        """CallSiteContext rejects empty repo."""
        assume(len(repo.strip()) == 0)
        with pytest.raises(ValidationError):
            CallSiteContext(
                repo=repo,
                file="f",
                line=1,
                language="python",
                client_library="cl",
                method="m",
                path_template="pt",
            )

    @given(call_site_contexts())
    def test_roundtrip_via_model_dump(
        self,
        inputs: tuple[str, str, int, str, str, str, str, list[str], list[str]],
    ) -> None:
        """CallSiteContext roundtrips through model_dump()."""
        repo, file_path, line, lang, client_lib, method, path_tmpl, fields, surrounding = inputs
        original = CallSiteContext(
            repo=repo,
            file=file_path,
            line=line,
            language=lang,
            client_library=client_lib,
            method=method,
            path_template=path_tmpl,
            fields=fields,
            surrounding_lines=surrounding,
        )
        dumped = original.model_dump()
        reconstructed = CallSiteContext.model_validate(dumped)
        assert reconstructed == original


class TestChangeSummary:
    """Invariants about ChangeSummary model."""

    @given(change_summaries())
    def test_model_creation_with_valid_inputs(
        self,
        inputs: tuple[str, str, str, str, str, str],
    ) -> None:
        """ChangeSummary can be created with valid inputs."""
        change_id, kind, location, verdict, rule_id, rationale = inputs
        summary = ChangeSummary(
            change_id=change_id,
            kind=kind,
            location=location,
            verdict=verdict,
            rule_id=rule_id,
            rationale=rationale,
        )
        assert summary.change_id == change_id
        assert summary.kind == kind
        assert summary.location == location
        assert summary.verdict == verdict
        assert summary.rule_id == rule_id
        assert summary.rationale == rationale

    @given(st.sampled_from(["invalid", "unknown", "neutral"]))
    def test_invalid_verdict_rejected(self, invalid_verdict: str) -> None:
        """ChangeSummary rejects verdicts not in {additive, behavioral, breaking}."""
        with pytest.raises(ValidationError):
            ChangeSummary(
                change_id="id",
                kind="k",
                location="l",
                verdict=invalid_verdict,  # type: ignore
                rule_id="r",
                rationale="rat",
            )

    @given(change_summaries())
    def test_roundtrip_via_model_dump(
        self,
        inputs: tuple[str, str, str, str, str, str],
    ) -> None:
        """ChangeSummary roundtrips through model_dump()."""
        change_id, kind, location, verdict, rule_id, rationale = inputs
        original = ChangeSummary(
            change_id=change_id,
            kind=kind,
            location=location,
            verdict=verdict,
            rule_id=rule_id,
            rationale=rationale,
        )
        dumped = original.model_dump()
        reconstructed = ChangeSummary.model_validate(dumped)
        assert reconstructed == original
