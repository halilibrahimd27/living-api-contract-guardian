"""Tree-sitter syntax validation for code blocks lifted out of guide markdown.

Generated migration guides are markdown documents with fenced code
blocks. Before persisting a guide we extract those blocks, parse them
with the same ``tree_sitter_languages`` parsers the M2 miner uses, and
reject the whole guide if any block fails to parse — no execution, no
typing. On rejection :class:`GuideService` retries with a stricter
prompt up to a configured limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from tree_sitter import Node, Parser
from tree_sitter_languages import get_parser

_FENCE_RE = re.compile(
    r"```(?P<lang>[A-Za-z0-9_+-]+)?[ \t]*\n(?P<body>.*?)\n```",
    re.DOTALL,
)

# Markdown fence aliases → tree_sitter_languages key.
_LANG_ALIASES: dict[str, str] = {
    "py": "python",
    "python": "python",
    "python3": "python",
    "js": "javascript",
    "javascript": "javascript",
    "node": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
    "tsx": "tsx",
    "jsx": "javascript",
}


class SnippetParseError(ValueError):
    """Raised when a fenced code block fails to parse cleanly."""

    def __init__(self, language: str, snippet: str, *, reason: str) -> None:
        super().__init__(f"snippet ({language}) failed to parse: {reason}")
        self.language = language
        self.snippet = snippet
        self.reason = reason


@dataclass(frozen=True)
class CodeBlock:
    """A single fenced code block lifted from markdown."""

    language: str  # canonical language id (e.g. "python")
    body: str


class SnippetReport(BaseModel):
    """Outcome of validating all fenced code blocks in a markdown guide."""

    model_config = ConfigDict(extra="forbid")

    total_blocks: int
    validated_blocks: int
    skipped_blocks: int


def extract_code_blocks(markdown: str) -> list[CodeBlock]:
    """Return every fenced code block in ``markdown``.

    Blocks without a language tag (plain ``` ``` ```) are included with
    an empty ``language`` — they are reported but not parsed.
    """
    out: list[CodeBlock] = []
    for match in _FENCE_RE.finditer(markdown):
        lang = (match.group("lang") or "").strip().lower()
        body = match.group("body")
        out.append(CodeBlock(language=lang, body=body))
    return out


def _has_error(node: Node) -> bool:
    if node.has_error:
        return True
    # tree_sitter 0.20 surfaces ``has_error`` only on the root subtree;
    # walking explicit ERROR nodes catches recoverable parses where the
    # parser inserted a placeholder.
    if node.type == "ERROR":
        return True
    for child in node.children:
        if _has_error(child):
            return True
    return False


def _parser_for(language: str) -> Parser | None:
    canonical = _LANG_ALIASES.get(language)
    if canonical is None:
        return None
    try:
        parser: Parser = get_parser(canonical)
    except Exception:
        return None
    return parser


def validate_markdown_snippets(markdown: str) -> SnippetReport:
    """Parse every fenced block in ``markdown`` with tree-sitter.

    A block whose language is unknown or untagged is *skipped* (the
    guide isn't penalised for a prose-only fence). A block in a known
    language that fails to parse raises :class:`SnippetParseError`,
    which :class:`GuideService` catches to trigger a stricter-prompt
    retry.
    """
    blocks = extract_code_blocks(markdown)
    validated = 0
    skipped = 0
    for block in blocks:
        parser = _parser_for(block.language)
        if parser is None:
            skipped += 1
            continue
        tree = parser.parse(block.body.encode("utf-8"))
        if _has_error(tree.root_node):
            raise SnippetParseError(
                language=block.language,
                snippet=block.body,
                reason="tree-sitter reported a parse error",
            )
        validated += 1
    return SnippetReport(
        total_blocks=len(blocks),
        validated_blocks=validated,
        skipped_blocks=skipped,
    )
