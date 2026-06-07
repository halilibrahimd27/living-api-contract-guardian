"""Match observed URLs against known OpenAPI route trees.

Given a static OpenAPI spec, build a trie keyed by URL path segments,
where a templated segment (e.g. ``{user_id}``) matches any non-empty
literal. When we see an observed URL we first walk the trie; if no
template matches we fall back to the heuristic segmentation in
``guardian_core.mining.path_normalize`` (numeric / UUID → ``{id}``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from guardian_core.mining.path_normalize import abstract_static_segments

_TEMPLATE_RE = re.compile(r"^\{([^}]+)\}$")


@dataclass
class _Node:
    children: dict[str, _Node] = field(default_factory=dict)
    template_child: _Node | None = None
    template_name: str | None = None
    terminals: set[str] = field(default_factory=set)  # methods registered at this node
    path_template: str | None = None  # original openapi path string


@dataclass
class RouteTree:
    """Trie of OpenAPI paths supporting templated-segment matching."""

    root: _Node = field(default_factory=_Node)

    def add_path(self, path: str, methods: list[str]) -> None:
        segments = [s for s in path.split("/") if s]
        node = self.root
        for seg in segments:
            m = _TEMPLATE_RE.match(seg)
            if m is not None:
                if node.template_child is None:
                    node.template_child = _Node()
                    node.template_child.template_name = m.group(1)
                node = node.template_child
            else:
                node = node.children.setdefault(seg, _Node())
        node.path_template = path
        for method in methods:
            node.terminals.add(method.upper())

    def match(self, method: str, path: str) -> str | None:
        """Return the OpenAPI template matching ``(method, path)``, or None."""
        segments = [s for s in path.split("/") if s]
        node = self.root
        method_u = method.upper()
        for seg in segments:
            if seg in node.children:
                node = node.children[seg]
            elif node.template_child is not None:
                node = node.template_child
            else:
                return None
        if node.path_template is not None and method_u in node.terminals:
            return node.path_template
        return None


def build_route_tree(openapi_spec: dict[str, Any]) -> RouteTree:
    """Build a ``RouteTree`` from an OpenAPI 3.x ``paths`` block."""
    tree = RouteTree()
    paths = openapi_spec.get("paths") if isinstance(openapi_spec, dict) else None
    if not isinstance(paths, dict):
        return tree
    for path, ops in paths.items():
        if not isinstance(path, str):
            continue
        methods: list[str] = []
        if isinstance(ops, dict):
            for k in ops.keys():
                if isinstance(k, str) and k.lower() in {
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                    "trace",
                }:
                    methods.append(k)
        if not methods:
            methods = ["GET"]
        tree.add_path(path, methods)
    return tree


def _strip_to_path(url: str) -> str:
    if "://" in url:
        parts = urlsplit(url)
        path = parts.path or "/"
    else:
        path = url.split("?", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    return path


def normalize_observed_path(
    url: str,
    method: str,
    tree: RouteTree | None = None,
) -> tuple[str, str | None]:
    """Return ``(templated_path, matched_openapi_template)`` for an observed URL.

    If ``tree`` provides a static-contract match, the matched OpenAPI
    template is returned as both the path and the second element. Otherwise
    we apply heuristic abstraction (numeric / UUID → ``{id}``).
    """
    path = _strip_to_path(url)
    matched: str | None = None
    if tree is not None:
        matched = tree.match(method, path)
    if matched is not None:
        return matched, matched
    # Heuristic: collapse numeric / UUID path segments to ``{id}``.
    templated = abstract_static_segments(path) or path
    return templated, None
