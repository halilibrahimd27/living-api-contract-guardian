"""Normalize URL strings into OpenAPI-style path templates."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_PLACEHOLDER = "\x00P\x00"  # internal sentinel for placeholder slots

# Heuristics for "this segment looks like an id, not a literal" used when
# a static URL contains e.g. a numeric id or a UUID we want to abstract.
_NUMERIC_RE = re.compile(r"^\d+$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _strip_scheme(url: str) -> str:
    """Drop scheme + host so only path/query remain, preserving the path."""
    if "://" not in url:
        return url
    parts = urlsplit(url)
    suffix = parts.path or "/"
    if parts.query:
        suffix = f"{suffix}?{parts.query}"
    return suffix


def _label_placeholders(placeholders: list[str]) -> list[str]:
    """Disambiguate repeated placeholder names within one path."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for raw in placeholders:
        name = raw or "param"
        if name in seen:
            seen[name] += 1
            out.append(f"{name}{seen[name]}")
        else:
            seen[name] = 1
            out.append(name)
    return out


def normalize_template(raw: str, placeholders: list[str]) -> str:
    """Turn raw URL + ordered placeholder names into an OpenAPI template.

    ``raw`` already has placeholder slots replaced with ``_PLACEHOLDER``
    by the caller. We strip scheme/host, drop the query string, then
    splice the named slots back in as ``{name}`` segments.
    """
    path = _strip_scheme(raw)
    if "?" in path:
        path = path.split("?", 1)[0]
    if not path:
        path = "/"
    if not path.startswith("/"):
        path = "/" + path

    labeled = _label_placeholders(placeholders)
    parts = path.split(_PLACEHOLDER)
    pieces: list[str] = [parts[0]]
    for name, segment in zip(labeled, parts[1:], strict=False):
        pieces.append("{" + name + "}")
        pieces.append(segment)
    rendered = "".join(pieces)

    # Collapse repeated slashes; OpenAPI templates don't use them.
    while "//" in rendered:
        rendered = rendered.replace("//", "/")
    if len(rendered) > 1 and rendered.endswith("/"):
        rendered = rendered[:-1]
    return rendered


def abstract_static_segments(template: str) -> str:
    """Replace static numeric / UUID path segments with ``{id}`` slots."""
    segments = template.split("/")
    out: list[str] = []
    for seg in segments:
        if _NUMERIC_RE.match(seg) or _UUID_RE.match(seg):
            out.append("{id}")
        else:
            out.append(seg)
    return "/".join(out)


PLACEHOLDER_SENTINEL = _PLACEHOLDER
