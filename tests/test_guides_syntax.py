"""Unit tests for :mod:`guardian_guides.syntax` and the cache-key contract.

These do not touch the database or HTTP — they exercise the snippet
extractor / validator and the prompt-hash function directly so a
regression in any of those surfaces fast.
"""

from __future__ import annotations

import pytest
from guardian_guides import (
    PROMPT_VERSION,
    build_cache_key,
    extract_code_blocks,
    prompt_hash,
    validate_markdown_snippets,
)
from guardian_guides.syntax import SnippetParseError


def test_extract_code_blocks_finds_each_fence() -> None:
    md = (
        "intro\n"
        "```python\n"
        "x = 1\n"
        "```\n"
        "between\n"
        "```js\n"
        "const y = 2;\n"
        "```\n"
        "tail\n"
    )
    blocks = extract_code_blocks(md)
    assert [b.language for b in blocks] == ["python", "js"]
    assert blocks[0].body == "x = 1"
    assert blocks[1].body == "const y = 2;"


def test_validate_accepts_well_formed_python_snippet() -> None:
    md = "before\n```python\ndef f(x):\n    return x + 1\n```\nafter\n"
    report = validate_markdown_snippets(md)
    assert report.total_blocks == 1
    assert report.validated_blocks == 1
    assert report.skipped_blocks == 0


def test_validate_accepts_well_formed_javascript_snippet() -> None:
    md = "```javascript\nconst greet = (name) => `hi ${name}`;\n```\n"
    report = validate_markdown_snippets(md)
    assert report.validated_blocks == 1


def test_validate_skips_unknown_language() -> None:
    md = "```\nplain text\n```\n```fortran\nINTEGER X\n```\n"
    report = validate_markdown_snippets(md)
    assert report.total_blocks == 2
    assert report.skipped_blocks == 2
    assert report.validated_blocks == 0


def test_validate_rejects_broken_python_snippet() -> None:
    md = "```python\ndef broken(\n```\n"
    with pytest.raises(SnippetParseError) as excinfo:
        validate_markdown_snippets(md)
    assert excinfo.value.language == "python"


def test_validate_rejects_broken_typescript_snippet() -> None:
    md = "```typescript\nfunction f(): { return\n```\n"
    with pytest.raises(SnippetParseError):
        validate_markdown_snippets(md)


def test_prompt_hash_is_deterministic_across_calls() -> None:
    a = "some prompt text\nwith newlines"
    b = "some prompt text\nwith newlines"
    assert prompt_hash(a) == prompt_hash(b)
    assert prompt_hash(a) != prompt_hash(a + "x")


def test_build_cache_key_changes_with_any_input() -> None:
    base = build_cache_key(
        diff_id="diff1",
        client_id="acme/x",
        prompt_version=PROMPT_VERSION,
        model="m1",
    )
    assert base != build_cache_key(
        diff_id="diff2",
        client_id="acme/x",
        prompt_version=PROMPT_VERSION,
        model="m1",
    )
    assert base != build_cache_key(
        diff_id="diff1",
        client_id="acme/y",
        prompt_version=PROMPT_VERSION,
        model="m1",
    )
    assert base != build_cache_key(
        diff_id="diff1",
        client_id="acme/x",
        prompt_version="vNEXT",
        model="m1",
    )
    assert base != build_cache_key(
        diff_id="diff1",
        client_id="acme/x",
        prompt_version=PROMPT_VERSION,
        model="m2",
    )
