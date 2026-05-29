"""JavaScript / TypeScript AST visitor — extracts fetch / axios call sites."""

from __future__ import annotations

from typing import Any

from tree_sitter import Node
from tree_sitter_languages import get_parser

from guardian_core.mining.models import InferredCallSite, Language
from guardian_core.mining.path_normalize import (
    PLACEHOLDER_SENTINEL,
    abstract_static_segments,
    normalize_template,
)

_AXIOS_VERBS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "request"}
)


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class JsVisitor:
    """Single-file JS/TS visitor producing :class:`InferredCallSite`s."""

    def __init__(self, file_path: str, source: bytes, language: Language) -> None:
        self.file_path = file_path
        self.source = source
        self.language = language
        # axios alias -> "axios" (so we recognise `import api from "axios"`).
        self.axios_aliases: set[str] = {"axios"}
        # name -> string-literal value for top-level const bindings.
        self.string_consts: dict[str, str] = {}

    def visit(self) -> list[InferredCallSite]:
        parser_name = "tsx" if self.language == "typescript" else "javascript"
        parser = get_parser(parser_name)
        tree = parser.parse(self.source)
        root = tree.root_node
        self._collect_imports_and_consts(root)
        results: list[InferredCallSite] = []
        self._walk(root, results)
        return results

    # ------------------------------------------------------------- imports

    def _collect_imports_and_consts(self, root: Node) -> None:
        for child in root.children:
            if child.type == "import_statement":
                self._handle_import(child)
            elif child.type == "lexical_declaration":
                self._handle_lexical_declaration(child)
            elif child.type == "variable_declaration":
                self._handle_lexical_declaration(child)

    def _handle_import(self, node: Node) -> None:
        source_node = node.child_by_field_name("source")
        if source_node is None:
            return
        source = _string_value(source_node, self.source)
        if source != "axios":
            return
        for child in node.named_children:
            if child.type == "import_clause":
                for sub in child.named_children:
                    if sub.type == "identifier":
                        self.axios_aliases.add(_text(sub, self.source))
                    elif sub.type == "namespace_import":
                        for sub2 in sub.named_children:
                            if sub2.type == "identifier":
                                self.axios_aliases.add(_text(sub2, self.source))

    def _handle_lexical_declaration(self, node: Node) -> None:
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            value_node = declarator.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            if name_node.type != "identifier":
                continue
            name = _text(name_node, self.source)
            if value_node.type == "string":
                lit = _string_value(value_node, self.source)
                if lit is not None:
                    self.string_consts[name] = lit
            elif value_node.type == "template_string":
                rendered, ph = self._render_template_string(value_node)
                if rendered is not None and not ph:
                    self.string_consts[name] = rendered
            elif value_node.type == "call_expression":
                # require("axios")
                callee = value_node.child_by_field_name("function")
                args = value_node.child_by_field_name("arguments")
                if (
                    callee is not None
                    and callee.type == "identifier"
                    and _text(callee, self.source) == "require"
                    and args is not None
                ):
                    for child in args.named_children:
                        if child.type == "string" and _string_value(child, self.source) == "axios":
                            self.axios_aliases.add(name)

    # --------------------------------------------------------------- walk

    def _walk(self, node: Node, out: list[InferredCallSite]) -> None:
        if node.type == "call_expression":
            site = self._inspect_call(node)
            if site is not None:
                out.append(site)
        for child in node.children:
            self._walk(child, out)

    # -------------------------------------------------------- call detection

    def _inspect_call(self, call: Node) -> InferredCallSite | None:
        func = call.child_by_field_name("function")
        args = call.child_by_field_name("arguments")
        if func is None or args is None:
            return None

        # fetch(url, init)
        if func.type == "identifier" and _text(func, self.source) == "fetch":
            return self._build_fetch(call, args)

        # axios.get(...), axios.post(...), axios.request(...)
        if func.type == "member_expression":
            obj = func.child_by_field_name("object")
            prop = func.child_by_field_name("property")
            if obj is not None and prop is not None and obj.type == "identifier":
                obj_name = _text(obj, self.source)
                if obj_name in self.axios_aliases:
                    verb = _text(prop, self.source).lower()
                    if verb in _AXIOS_VERBS:
                        return self._build_axios(call, args, verb)

        # axios(config) — call directly on alias.
        if func.type == "identifier" and _text(func, self.source) in self.axios_aliases:
            return self._build_axios(call, args, "request")
        return None

    # --------------------------------------------------------- fetch / axios

    def _build_fetch(self, call: Node, args: Node) -> InferredCallSite | None:
        positional = self._positional_args(args)
        if not positional:
            return None
        url_node = positional[0]
        template, ph_names = self._render_url(url_node)
        if template is None:
            return None
        path_template = normalize_template(template, ph_names)
        path_template = abstract_static_segments(path_template)

        method = "GET"
        fields: list[str] = []
        if len(positional) >= 2 and positional[1].type == "object":
            method = self._object_method(positional[1]) or "GET"
            fields = self._object_body_keys(positional[1])
        return InferredCallSite(
            file=self.file_path,
            line=call.start_point[0] + 1,
            language=self.language,
            client_library="fetch",
            method=method.upper(),
            path_template=path_template,
            fields=sorted(set(fields)),
        )

    def _build_axios(self, call: Node, args: Node, verb: str) -> InferredCallSite | None:
        positional = self._positional_args(args)
        method = verb.upper()
        url_node: Node | None = None
        config_node: Node | None = None

        if verb == "request":
            # axios.request({url, method, data, params})
            if positional and positional[0].type == "object":
                config_node = positional[0]
                method = (self._object_method(config_node) or "GET").upper()
                url_node = self._object_field(config_node, "url")
        elif verb in {"get", "delete", "head", "options"}:
            if positional:
                url_node = positional[0]
            if len(positional) >= 2 and positional[1].type == "object":
                config_node = positional[1]
        else:  # post / put / patch
            if positional:
                url_node = positional[0]
            if len(positional) >= 3 and positional[2].type == "object":
                config_node = positional[2]

        if url_node is None:
            return None
        template, ph_names = self._render_url(url_node)
        if template is None:
            return None
        path_template = normalize_template(template, ph_names)
        path_template = abstract_static_segments(path_template)

        fields: list[str] = []
        if verb in {"post", "put", "patch"} and len(positional) >= 2:
            fields.extend(self._extract_body_fields(positional[1]))
        if config_node is not None:
            data_node = self._object_field(config_node, "data")
            params_node = self._object_field(config_node, "params")
            if data_node is not None:
                fields.extend(self._extract_body_fields(data_node))
            if params_node is not None:
                fields.extend(self._extract_body_fields(params_node))
        return InferredCallSite(
            file=self.file_path,
            line=call.start_point[0] + 1,
            language=self.language,
            client_library="axios",
            method=method,
            path_template=path_template,
            fields=sorted(set(fields)),
        )

    # ------------------------------------------------------- args / objects

    def _positional_args(self, args: Node) -> list[Node]:
        out: list[Node] = []
        for child in args.named_children:
            if child.type in {"comment"}:
                continue
            out.append(child)
        return out

    def _object_field(self, obj: Node, name: str) -> Node | None:
        for child in obj.named_children:
            if child.type != "pair":
                continue
            key = child.child_by_field_name("key")
            value = child.child_by_field_name("value")
            if key is None or value is None:
                continue
            key_name = _key_text(key, self.source)
            if key_name == name:
                return value
        return None

    def _object_method(self, obj: Node) -> str | None:
        method_node = self._object_field(obj, "method")
        if method_node is None:
            return None
        if method_node.type == "string":
            return _string_value(method_node, self.source)
        return None

    def _object_body_keys(self, obj: Node) -> list[str]:
        keys: list[str] = []
        body = self._object_field(obj, "body")
        if body is not None and body.type == "call_expression":
            callee = body.child_by_field_name("function")
            if callee is not None and callee.type == "member_expression":
                prop = callee.child_by_field_name("property")
                if prop is not None and _text(prop, self.source) == "stringify":
                    body_args = body.child_by_field_name("arguments")
                    if body_args is not None:
                        for child in body_args.named_children:
                            keys.extend(self._object_keys(child))
        # JSON-ish body shorthand: `body: { name: ... }`
        if body is not None and body.type == "object":
            keys.extend(self._object_keys(body))
        return keys

    def _extract_body_fields(self, node: Node) -> list[str]:
        if node.type == "object":
            return self._object_keys(node)
        return []

    def _object_keys(self, node: Node) -> list[str]:
        if node.type != "object":
            return []
        out: list[str] = []
        for child in node.named_children:
            if child.type != "pair":
                continue
            key = child.child_by_field_name("key")
            if key is None:
                continue
            name = _key_text(key, self.source)
            if name is not None:
                out.append(name)
        return out

    # ----------------------------------------------------- url rendering

    def _render_url(self, node: Node) -> tuple[str | None, list[str]]:
        if node.type == "string":
            literal = _string_value(node, self.source)
            return (literal if literal is not None else None), []
        if node.type == "template_string":
            return self._render_template_string(node)
        if node.type == "identifier":
            name = _text(node, self.source)
            if name in self.string_consts:
                return self.string_consts[name], []
            return None, []
        if node.type == "binary_expression":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            op = node.child_by_field_name("operator")
            if left is None or right is None or op is None:
                return None, []
            if _text(op, self.source) != "+":
                return None, []
            l_url, l_ph = self._render_url(left)
            r_url, r_ph = self._render_url(right)
            if l_url is None or r_url is None:
                return None, []
            return l_url + r_url, l_ph + r_ph
        return None, []

    def _render_template_string(self, node: Node) -> tuple[str | None, list[str]]:
        # The JS grammar shipped with tree-sitter-languages 1.10 does not
        # always expose literal portions of a template string as named
        # `string_fragment` children — they live in the byte gap between
        # the backticks and any `template_substitution`s. Reconstruct by
        # walking the byte range and slicing the gaps directly.
        text_start = node.start_byte + 1  # skip opening backtick
        text_end = node.end_byte - 1  # skip closing backtick
        pieces: list[str] = []
        placeholders: list[str] = []
        cursor = text_start
        for child in node.children:
            if child.type != "template_substitution":
                continue
            if child.start_byte > cursor:
                pieces.append(
                    self.source[cursor : child.start_byte].decode("utf-8", errors="replace")
                )
            pieces.append(PLACEHOLDER_SENTINEL)
            placeholders.append(_template_substitution_name(child, self.source))
            cursor = child.end_byte
        if cursor < text_end:
            pieces.append(self.source[cursor:text_end].decode("utf-8", errors="replace"))

        rendered_pieces: list[str] = []
        kept_ph: list[str] = []
        ph_iter = iter(placeholders)
        for piece in pieces:
            if piece == PLACEHOLDER_SENTINEL:
                name = next(ph_iter)
                resolved = self.string_consts.get(name)
                if resolved is not None:
                    rendered_pieces.append(resolved)
                else:
                    rendered_pieces.append(PLACEHOLDER_SENTINEL)
                    kept_ph.append(name)
            else:
                rendered_pieces.append(piece)
        return "".join(rendered_pieces), kept_ph

    def _iter_descendants(self, root: Node) -> Any:
        stack: list[Node] = [root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))


def _string_value(node: Node, src: bytes) -> str | None:
    """Return the literal value of a plain string node."""
    parts: list[str] = []
    for child in node.children:
        if child.type == "string_fragment":
            parts.append(_text(child, src))
        elif child.type == "escape_sequence":
            parts.append(_text(child, src))
    if not parts and node.named_child_count == 0:
        # Empty literal "" / ''
        raw = _text(node, src)
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
            return ""
    return "".join(parts) if parts else None


def _key_text(node: Node, src: bytes) -> str | None:
    if node.type == "property_identifier":
        return _text(node, src)
    if node.type == "identifier":
        return _text(node, src)
    if node.type == "string":
        return _string_value(node, src)
    if node.type == "computed_property_name":
        for child in node.named_children:
            if child.type == "string":
                return _string_value(child, src)
    return None


def _template_substitution_name(node: Node, src: bytes) -> str:
    for child in node.named_children:
        if child.type == "identifier":
            return _text(child, src)
        if child.type == "member_expression":
            prop = child.child_by_field_name("property")
            if prop is not None:
                return _text(prop, src)
    return "param"
