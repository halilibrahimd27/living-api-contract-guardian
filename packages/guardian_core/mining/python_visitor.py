"""Python AST visitor — extracts requests/httpx/gRPC call sites."""

from __future__ import annotations

from typing import Any

from tree_sitter import Node
from tree_sitter_languages import get_parser

from guardian_core.mining.models import InferredCallSite
from guardian_core.mining.path_normalize import (
    PLACEHOLDER_SENTINEL,
    abstract_static_segments,
    normalize_template,
)

# requests / httpx verb methods we recognise as HTTP calls.
_HTTP_VERBS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "request"}
)

# Module aliases observable from `import X as Y` / `from X import Y`.
_HTTP_MODULES: frozenset[str] = frozenset({"requests", "httpx"})


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class PythonVisitor:
    """Single-file Python visitor producing :class:`InferredCallSite`s."""

    def __init__(self, file_path: str, source: bytes) -> None:
        self.file_path = file_path
        self.source = source
        # name -> module path it points at (e.g. {"r": "requests", "httpx": "httpx"})
        self.module_aliases: dict[str, str] = {}
        # name -> origin module of a "from X import name" binding.
        self.from_imports: dict[str, str] = {}
        # name -> string-literal value of `NAME = "..."` at module scope.
        self.string_consts: dict[str, str] = {}
        # name -> "module.member" for `stub = pkg_pb2_grpc.FooStub(channel)`.
        self.grpc_stubs: dict[str, str] = {}
        # gRPC modules detected via import (set of short names ending in _pb2_grpc).
        self.grpc_modules: set[str] = set()
        # name -> module ("httpx" / "requests") for variables bound to a
        # `httpx.Client(...)` / `httpx.AsyncClient(...)` / `requests.Session()`
        # so `client.get(...)` is recognised as an HTTP call.
        self.http_clients: dict[str, str] = {}

    # ------------------------------------------------------------------ entry

    def visit(self) -> list[InferredCallSite]:
        parser = get_parser("python")
        tree = parser.parse(self.source)
        root = tree.root_node
        self._collect_imports_and_consts(root)
        results: list[InferredCallSite] = []
        self._walk(root, results)
        return results

    # ------------------------------------------------------------- imports

    def _collect_imports_and_consts(self, root: Node) -> None:
        for child in self._iter_descendants(root):
            if child.type == "import_statement":
                self._handle_import_statement(child)
            elif child.type == "import_from_statement":
                self._handle_import_from(child)
            elif child.type == "assignment":
                self._handle_module_assignment(child)
            elif child.type == "as_pattern":
                self._handle_as_pattern(child)

    def _handle_import_statement(self, node: Node) -> None:
        for child in node.named_children:
            if child.type == "dotted_name":
                name = _text(child, self.source)
                self.module_aliases[name] = name
                if name.endswith("_pb2_grpc"):
                    self.grpc_modules.add(name)
            elif child.type == "aliased_import":
                modname_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if modname_node is None or alias_node is None:
                    continue
                modname = _text(modname_node, self.source)
                alias = _text(alias_node, self.source)
                self.module_aliases[alias] = modname
                if modname.endswith("_pb2_grpc"):
                    self.grpc_modules.add(alias)

    def _handle_import_from(self, node: Node) -> None:
        module_node = node.child_by_field_name("module_name")
        module = _text(module_node, self.source) if module_node is not None else ""
        is_grpc_mod = module.endswith("_pb2_grpc")
        for child in node.named_children:
            if child == module_node:
                continue
            if child.type == "dotted_name":
                name = _text(child, self.source)
                self.from_imports[name] = module
                if is_grpc_mod:
                    self.grpc_modules.add(name)
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node is None or alias_node is None:
                    continue
                alias = _text(alias_node, self.source)
                self.from_imports[alias] = module
                if is_grpc_mod:
                    self.grpc_modules.add(alias)

    def _handle_as_pattern(self, node: Node) -> None:
        """Track ``with httpx.Client() as client`` style aliases."""
        # children: [value_expr, "as", as_pattern_target]
        value = node.children[0] if node.children else None
        target_node: Node | None = None
        for child in node.named_children:
            if child.type == "as_pattern_target":
                target_node = child
                break
        if value is None or target_node is None:
            return
        target_ident: Node | None = None
        for child in target_node.named_children:
            if child.type == "identifier":
                target_ident = child
                break
        if target_ident is None:
            return
        module = self._classify_client_constructor(value)
        if module is None:
            return
        self.http_clients[_text(target_ident, self.source)] = module

    def _classify_client_constructor(self, node: Node) -> str | None:
        """Return the http module name if ``node`` is e.g. ``httpx.Client()``."""
        if node.type != "call":
            return None
        func = node.child_by_field_name("function")
        if func is None or func.type != "attribute":
            return None
        obj = func.child_by_field_name("object")
        attr = func.child_by_field_name("attribute")
        if obj is None or attr is None or obj.type != "identifier":
            return None
        obj_name = _text(obj, self.source)
        attr_name = _text(attr, self.source)
        module = self.module_aliases.get(obj_name) or self.from_imports.get(obj_name)
        if module is None and obj_name in _HTTP_MODULES:
            module = obj_name
        if module is None or module.split(".")[0] not in _HTTP_MODULES:
            return None
        if attr_name not in {"Client", "AsyncClient", "Session"}:
            return None
        return module

    def _handle_module_assignment(self, node: Node) -> None:
        # String constants are tracked at module scope only (cheap, file-wide
        # propagation). Stub and http-client bindings are tracked at any scope
        # since they typically live inside a function body.
        target = node.child_by_field_name("left")
        value = node.child_by_field_name("right")
        if target is None or value is None or target.type != "identifier":
            return
        name = _text(target, self.source)
        is_module_scope = (
            node.parent is not None
            and node.parent.type == "expression_statement"
            and node.parent.parent is not None
            and node.parent.parent.type == "module"
        )
        if value.type == "string":
            if is_module_scope:
                lit = _string_literal_value(value, self.source)
                if lit is not None:
                    self.string_consts[name] = lit
            return
        if value.type == "call":
            # client = httpx.Client() / requests.Session() — treat the binding
            # as an HTTP client we can resolve `.get/.post/...` against.
            http_mod = self._classify_client_constructor(value)
            if http_mod is not None:
                self.http_clients[name] = http_mod
                return
            # stub = pkg.FooStub(channel)
            func = value.child_by_field_name("function")
            if func is None or func.type != "attribute":
                return
            obj = func.child_by_field_name("object")
            attr = func.child_by_field_name("attribute")
            if obj is None or attr is None or obj.type != "identifier":
                return
            obj_name = _text(obj, self.source)
            attr_name = _text(attr, self.source)
            mod = self.module_aliases.get(obj_name) or self.from_imports.get(obj_name)
            if mod is None or not mod.endswith("_pb2_grpc"):
                return
            if not attr_name.endswith("Stub"):
                return
            self.grpc_stubs[name] = f"{mod}.{attr_name}"

    # -------------------------------------------------------------- walk

    def _walk(self, node: Node, out: list[InferredCallSite]) -> None:
        if node.type == "call":
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

        # Pattern 1: requests.get(...) / httpx.post(...) — attribute on module alias.
        attr_match = self._match_http_attribute(func)
        if attr_match is not None:
            module, verb = attr_match
            return self._build_http_site(call, args, module, verb)

        # Pattern 2: httpx.AsyncClient.post(...) (chained attribute).
        chained = self._match_chained_http(func)
        if chained is not None:
            module, verb = chained
            return self._build_http_site(call, args, module, verb)

        # Pattern 3: gRPC stub method invocation — stub.MethodName(req).
        grpc = self._match_grpc_stub_call(func)
        if grpc is not None:
            stub_path, method = grpc
            return self._build_grpc_site(call, args, stub_path, method)
        return None

    def _match_http_attribute(self, func: Node) -> tuple[str, str] | None:
        if func.type != "attribute":
            return None
        obj = func.child_by_field_name("object")
        attr = func.child_by_field_name("attribute")
        if obj is None or attr is None or obj.type != "identifier":
            return None
        verb = _text(attr, self.source).lower()
        if verb not in _HTTP_VERBS:
            return None
        obj_name = _text(obj, self.source)
        module = self.module_aliases.get(obj_name) or self.from_imports.get(obj_name)
        if module is None and obj_name in _HTTP_MODULES:
            module = obj_name
        if module is None and obj_name in self.http_clients:
            module = self.http_clients[obj_name]
        if module is None:
            return None
        if module.split(".")[0] not in _HTTP_MODULES:
            return None
        return module, verb

    def _match_chained_http(self, func: Node) -> tuple[str, str] | None:
        # e.g. httpx.AsyncClient.post(...) — outer attribute's object is
        # itself an attribute whose ultimate root is a known module.
        if func.type != "attribute":
            return None
        attr = func.child_by_field_name("attribute")
        obj = func.child_by_field_name("object")
        if attr is None or obj is None or obj.type != "attribute":
            return None
        verb = _text(attr, self.source).lower()
        if verb not in _HTTP_VERBS:
            return None
        root = obj
        while root.type == "attribute":
            nxt = root.child_by_field_name("object")
            if nxt is None:
                return None
            root = nxt
        if root.type != "identifier":
            return None
        root_name = _text(root, self.source)
        module = self.module_aliases.get(root_name) or self.from_imports.get(root_name)
        if module is None and root_name in _HTTP_MODULES:
            module = root_name
        if module is None or module.split(".")[0] not in _HTTP_MODULES:
            return None
        return module, verb

    def _match_grpc_stub_call(self, func: Node) -> tuple[str, str] | None:
        if func.type != "attribute":
            return None
        obj = func.child_by_field_name("object")
        attr = func.child_by_field_name("attribute")
        if obj is None or attr is None or obj.type != "identifier":
            return None
        obj_name = _text(obj, self.source)
        if obj_name not in self.grpc_stubs:
            return None
        method = _text(attr, self.source)
        if not method or not method[0].isalpha():
            return None
        return self.grpc_stubs[obj_name], method

    # ----------------------------------------------------- site construction

    def _build_http_site(
        self, call: Node, args: Node, module: str, verb: str
    ) -> InferredCallSite | None:
        positional = self._positional_args(args)
        kwargs = self._keyword_args(args)

        # `request("METHOD", url, ...)` — verb is the first positional string.
        method = verb.upper()
        url_node: Node | None = None
        if verb == "request":
            if len(positional) >= 2 and positional[0].type == "string":
                literal = _string_literal_value(positional[0], self.source)
                if literal is not None:
                    method = literal.upper()
                url_node = positional[1]
            elif "method" in kwargs and "url" in kwargs:
                m_val = kwargs["method"]
                lit = _string_literal_value(m_val, self.source) if m_val.type == "string" else None
                if lit is not None:
                    method = lit.upper()
                url_node = kwargs["url"]
            else:
                return None
        else:
            if positional:
                url_node = positional[0]
            elif "url" in kwargs:
                url_node = kwargs["url"]

        if url_node is None:
            return None

        template, ph_names = self._render_url(url_node)
        if template is None:
            return None
        path_template = normalize_template(template, ph_names)
        path_template = abstract_static_segments(path_template)

        fields = self._collect_http_fields(kwargs)
        library = "requests" if module.startswith("requests") else "httpx"
        return InferredCallSite(
            file=self.file_path,
            line=call.start_point[0] + 1,
            language="python",
            client_library=library,
            method=method,
            path_template=path_template,
            fields=sorted(set(fields)),
        )

    def _build_grpc_site(
        self, call: Node, args: Node, stub_path: str, method: str
    ) -> InferredCallSite:
        # stub_path is "module._pb2_grpc.FooStub" — derive a "Foo/Method" template.
        stub_short = stub_path.rsplit(".", 1)[-1]
        if stub_short.endswith("Stub"):
            service_name = stub_short[: -len("Stub")]
        else:
            service_name = stub_short
        module_root = stub_path.split(".", 1)[0]
        package = (
            module_root[: -len("_pb2_grpc")] if module_root.endswith("_pb2_grpc") else module_root
        )
        template = f"/{package}.{service_name}/{method}"

        fields = self._collect_grpc_fields(args)
        return InferredCallSite(
            file=self.file_path,
            line=call.start_point[0] + 1,
            language="python",
            client_library="grpc",
            method="RPC",
            path_template=template,
            fields=sorted(set(fields)),
        )

    # ------------------------------------------------------- args inspection

    def _positional_args(self, args: Node) -> list[Node]:
        out: list[Node] = []
        for child in args.named_children:
            if child.type == "keyword_argument":
                continue
            if child.type in {"comment"}:
                continue
            out.append(child)
        return out

    def _keyword_args(self, args: Node) -> dict[str, Node]:
        out: dict[str, Node] = {}
        for child in args.named_children:
            if child.type != "keyword_argument":
                continue
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            out[_text(name_node, self.source)] = value_node
        return out

    def _collect_http_fields(self, kwargs: dict[str, Node]) -> list[str]:
        fields: list[str] = []
        for key in ("params", "json", "data", "files"):
            if key not in kwargs:
                continue
            value = kwargs[key]
            fields.extend(_dict_keys(value, self.source))
        return fields

    def _collect_grpc_fields(self, args: Node) -> list[str]:
        # `Stub.Method(Request(a=1, b=2))` — pull keyword names from the
        # first positional call's argument list, if it is a call.
        positional = self._positional_args(args)
        if not positional:
            return []
        first = positional[0]
        if first.type != "call":
            return []
        req_args = first.child_by_field_name("arguments")
        if req_args is None:
            return []
        names: list[str] = []
        for child in req_args.named_children:
            if child.type == "keyword_argument":
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    names.append(_text(name_node, self.source))
        return names

    # ---------------------------------------------------- url rendering

    def _render_url(self, node: Node) -> tuple[str | None, list[str]]:
        """Resolve a URL expression to (raw_with_sentinels, placeholder_names)."""
        if node.type == "string":
            return self._render_string(node)
        if node.type == "identifier":
            name = _text(node, self.source)
            if name in self.string_consts:
                return self.string_consts[name], []
            return None, []
        if node.type == "binary_operator":
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

    def _render_string(self, node: Node) -> tuple[str, list[str]]:
        """Render a (potentially f-)string into raw text + placeholder names."""
        pieces: list[str] = []
        placeholders: list[str] = []
        for child in node.children:
            if child.type == "string_content":
                pieces.append(_text(child, self.source))
            elif child.type == "escape_sequence":
                pieces.append(_text(child, self.source))
            elif child.type == "interpolation":
                pieces.append(PLACEHOLDER_SENTINEL)
                placeholders.append(_interpolation_name(child, self.source))
            # string_start / string_end are quote markers — skip.
        # Substitute constants where the f-string interpolates a known
        # module-level string constant (e.g. f"{URL}/items").
        rendered_pieces: list[str] = []
        ph_iter = iter(placeholders)
        kept_ph: list[str] = []
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

    # -------------------------------------------------------- ast walker

    def _iter_descendants(self, root: Node) -> Any:
        stack: list[Node] = [root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))


def _string_literal_value(node: Node, src: bytes) -> str | None:
    """Return the literal text of a plain (non-f) string, else ``None``."""
    parts: list[str] = []
    for child in node.children:
        if child.type == "string_start":
            text = _text(child, src)
            if "f" in text.lower():
                return None
        elif child.type == "string_content":
            parts.append(_text(child, src))
        elif child.type == "interpolation":
            return None
    return "".join(parts)


def _interpolation_name(node: Node, src: bytes) -> str:
    """Pick a placeholder name from an interpolation node."""
    for child in node.named_children:
        if child.type == "identifier":
            return _text(child, src)
        if child.type == "attribute":
            attr = child.child_by_field_name("attribute")
            if attr is not None:
                return _text(attr, src)
        if child.type == "call":
            func = child.child_by_field_name("function")
            if func is not None and func.type == "identifier":
                return _text(func, src)
    return "param"


def _dict_keys(node: Node, src: bytes) -> list[str]:
    """Extract string keys from a Python dict / call(**kw) expression."""
    if node.type != "dictionary":
        return []
    keys: list[str] = []
    for child in node.named_children:
        if child.type != "pair":
            continue
        key_node = child.child_by_field_name("key")
        if key_node is None or key_node.type != "string":
            continue
        lit = _string_literal_value(key_node, src)
        if lit is not None:
            keys.append(lit)
    return keys
