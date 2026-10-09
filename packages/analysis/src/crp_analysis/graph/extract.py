"""Per-file relation extraction with Tree-sitter (imports, requires, extends/implements).

Results are pure data (JSON-serializable) so they can be cached by blob hash and extractor
version. A file whose parse fails yields no relations; a partial parse keeps the relations from
well-formed regions and is flagged so consumers can warn.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from tree_sitter import Node, Parser

from crp_analysis.structure import _language, extractor_version, grammar_for

GRAPH_EXTRACTOR = "crp-graph-extract-v2"  # v2: abstract types marked (P10 metrics)
MAX_REFERENCES_PER_FILE = 2000
_WS = re.compile(r"\s+")
_GENERIC = re.compile(r"<.*$", re.DOTALL)


def graph_extractor_version() -> str:
    return f"{GRAPH_EXTRACTOR} ({extractor_version()})"


@dataclass(slots=True)
class Reference:
    """One outgoing reference as written in the source (target resolution happens later)."""

    relation: str  # imports | reexports | requires | dynamic_import | extends | implements
    target: str | None  # raw specifier or type name; None when not a literal
    line: int
    end_line: int
    owner: str | None = None  # declaring type for extends/implements
    static: bool = False
    wildcard: bool = False
    type_only: bool = False
    text: str = ""  # normalized source line at ``line`` (evidence), at most 300 characters


@dataclass(slots=True)
class DeclaredType:
    name: str  # qualified by enclosing types, e.g. Outer.Inner
    kind: str
    line: int
    end_line: int
    abstract: bool = False  # interfaces and abstract classes (abstractness metric, P10)


@dataclass(slots=True)
class Binding:
    """A local name introduced by an import: ``local`` comes from ``specifier``/``imported``."""

    local: str
    specifier: str
    imported: str  # exported name, "default" or "*"


@dataclass(slots=True)
class FileFacts:
    status: str  # OK | PARTIAL | FAILED | UNSUPPORTED
    error_count: int = 0
    package: str | None = None
    types: list[DeclaredType] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    bindings: list[Binding] = field(default_factory=list)
    truncated: bool = False
    message: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FileFacts:
        return cls(
            status=data["status"],
            error_count=data.get("error_count", 0),
            package=data.get("package"),
            types=[DeclaredType(**t) for t in data.get("types", [])],
            references=[Reference(**r) for r in data.get("references", [])],
            bindings=[Binding(**b) for b in data.get("bindings", [])],
            truncated=data.get("truncated", False),
            message=data.get("message"),
        )


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _type_name(node: Node) -> str:
    """``List<String>`` -> ``List``; ``a.b.C`` -> ``a.b.C``; whitespace removed."""
    if node.type == "generic_type":
        base = node.child_by_field_name("name") or (
            node.named_children[0] if node.named_children else None
        )
        return _type_name(base) if base is not None else ""
    return _GENERIC.sub("", _WS.sub("", _text(node)))


def _string_literal(node: Node | None) -> str | None:
    """Literal value of a string / substitution-free template string, else None."""
    if node is None:
        return None
    if node.type == "string":
        return "".join(_text(c) for c in node.named_children if c.type == "string_fragment")
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.named_children):
            return None
        return "".join(_text(c) for c in node.named_children if c.type == "string_fragment")
    return None


class _Collector:
    def __init__(self, facts: FileFacts, lines: list[str]) -> None:
        self.facts = facts
        self.lines = lines

    def add(self, ref: Reference) -> None:
        if len(self.facts.references) >= MAX_REFERENCES_PER_FILE:
            self.facts.truncated = True
            return
        if 1 <= ref.line <= len(self.lines):
            ref.text = _WS.sub(" ", self.lines[ref.line - 1]).strip()[:300]
        self.facts.references.append(ref)


def _lines(node: Node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


# -- Java ---------------------------------------------------------------------------------------


def _java(root: Node, facts: FileFacts, lines: list[str]) -> None:
    out = _Collector(facts, lines)
    stack: list[tuple[Node, str | None]] = [(root, None)]
    while stack:
        node, outer = stack.pop()
        kind = node.type
        if kind == "package_declaration":
            name = next((c for c in node.named_children if "identifier" in c.type), None)
            facts.package = _WS.sub("", _text(name)) or None
            continue
        if kind == "import_declaration":
            path = next((c for c in node.named_children if "identifier" in c.type), None)
            start, end = _lines(node)
            out.add(
                Reference(
                    "imports",
                    _WS.sub("", _text(path)) or None,
                    start,
                    end,
                    static=any(c.type == "static" for c in node.children),
                    wildcard=any(c.type == "asterisk" for c in node.children),
                )
            )
            continue
        next_outer = outer
        if kind in {
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "record_declaration",
            "annotation_type_declaration",
        }:
            simple = _text(node.child_by_field_name("name"))
            if simple:
                qualified = f"{outer}.{simple}" if outer else simple
                start, end = _lines(node)
                abstract = kind in {"interface_declaration", "annotation_type_declaration"} or (
                    kind == "class_declaration"
                    and any(
                        child.type == "modifiers"
                        and any(m.type == "abstract" for m in child.children)
                        for child in node.children
                    )
                )
                facts.types.append(
                    DeclaredType(qualified, kind.removesuffix("_declaration"), start, end, abstract)
                )
                next_outer = qualified
                for child in node.children:
                    if child.type == "superclass":
                        for t in child.named_children:
                            _java_heritage(out, "extends", t, qualified)
                    elif child.type in {"super_interfaces", "extends_interfaces"}:
                        relation = "implements" if child.type == "super_interfaces" else "extends"
                        for type_list in child.named_children:
                            for t in type_list.named_children:
                                _java_heritage(out, relation, t, qualified)
        stack.extend((child, next_outer) for child in reversed(node.children))


def _java_heritage(out: _Collector, relation: str, node: Node, owner: str) -> None:
    name = _type_name(node)
    if name:
        start, end = _lines(node)
        out.add(Reference(relation, name, start, end, owner=owner))


# -- JavaScript / TypeScript --------------------------------------------------------------------


def _js(root: Node, facts: FileFacts, lines: list[str]) -> None:
    out = _Collector(facts, lines)
    stack: list[tuple[Node, str | None]] = [(root, None)]
    while stack:
        node, outer = stack.pop()
        kind = node.type
        next_outer = outer
        if kind == "import_statement":
            _js_import(node, facts, out)
        elif kind == "export_statement" and node.child_by_field_name("source") is not None:
            start, end = _lines(node)
            spec = _string_literal(node.child_by_field_name("source"))
            out.add(Reference("reexports", spec, start, end))
        elif kind == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            if fn is not None and (
                fn.type == "import" or (fn.type == "identifier" and _text(fn) == "require")
            ):
                start, end = _lines(node)
                relation = "dynamic_import" if fn.type == "import" else "requires"
                out.add(Reference(relation, _string_literal(first), start, end))
        elif kind in {"class_declaration", "abstract_class_declaration", "interface_declaration"}:
            simple = _text(node.child_by_field_name("name"))
            if simple:
                start, end = _lines(node)
                label = "interface" if kind == "interface_declaration" else "class"
                abstract = kind != "class_declaration"
                facts.types.append(DeclaredType(simple, label, start, end, abstract))
                next_outer = simple
                for child in node.children:
                    if child.type == "class_heritage":
                        _js_heritage(child, out, simple)
                    elif child.type == "extends_type_clause":
                        for t in child.named_children:
                            _add_heritage(out, "extends", t, simple)
        stack.extend((child, next_outer) for child in reversed(node.children))


def _js_import(node: Node, facts: FileFacts, out: _Collector) -> None:
    start, end = _lines(node)
    type_only = any(c.type == "type" for c in node.children)
    require_clause = next((c for c in node.children if c.type == "import_require_clause"), None)
    if require_clause is not None:
        spec = _string_literal(require_clause.child_by_field_name("source"))
        out.add(Reference("imports", spec, start, end))
        local = next((c for c in require_clause.named_children if c.type == "identifier"), None)
        if spec is not None and local is not None:
            facts.bindings.append(Binding(_text(local), spec, "*"))
        return
    spec = _string_literal(node.child_by_field_name("source"))
    out.add(Reference("imports", spec, start, end, type_only=type_only))
    if spec is None:
        return
    for clause in (c for c in node.children if c.type == "import_clause"):
        for part in clause.named_children:
            if part.type == "identifier":
                facts.bindings.append(Binding(_text(part), spec, "default"))
            elif part.type == "namespace_import":
                ident = next((c for c in part.named_children if c.type == "identifier"), None)
                if ident is not None:
                    facts.bindings.append(Binding(_text(ident), spec, "*"))
            elif part.type == "named_imports":
                for item in (c for c in part.named_children if c.type == "import_specifier"):
                    name = _text(item.child_by_field_name("name"))
                    alias = _text(item.child_by_field_name("alias")) or name
                    if name:
                        facts.bindings.append(Binding(alias, spec, name))


def _js_heritage(heritage: Node, out: _Collector, owner: str) -> None:
    for child in heritage.named_children:
        if child.type == "extends_clause":
            value = child.child_by_field_name("value")
            if value is not None:
                _add_heritage(out, "extends", value, owner)
        elif child.type == "implements_clause":
            for t in child.named_children:
                _add_heritage(out, "implements", t, owner)
        else:  # JavaScript grammar: `extends <expression>` directly under class_heritage
            _add_heritage(out, "extends", child, owner)


def _add_heritage(out: _Collector, relation: str, node: Node, owner: str) -> None:
    if node.type in {
        "identifier",
        "type_identifier",
        "member_expression",
        "nested_type_identifier",
        "generic_type",
    }:
        name = _type_name(node)
        if name:
            start, end = _lines(node)
            out.add(Reference(relation, name, start, end, owner=owner))
    else:  # call expressions, mixins etc. are not statically resolvable
        start, end = _lines(node)
        out.add(Reference(relation, None, start, end, owner=owner))


def extract_facts(source: bytes, path: str, language: str | None) -> FileFacts:
    grammar = grammar_for(path, language)
    if grammar is None:
        return FileFacts("UNSUPPORTED", message="no grammar for this language")
    try:
        tree = Parser(_language(grammar)).parse(source)
    except (ValueError, RuntimeError) as exc:
        return FileFacts("FAILED", message=f"parser error: {type(exc).__name__}")
    facts = FileFacts("OK")
    lines = source.decode("utf-8", errors="replace").splitlines()
    if grammar == "java":
        _java(tree.root_node, facts, lines)
    else:
        _js(tree.root_node, facts, lines)
    if tree.root_node.has_error:
        facts.status = "PARTIAL"
        facts.error_count = _error_count(tree.root_node)
        facts.message = (
            f"{max(facts.error_count, 1)} syntax error node(s); "
            "relations come from well-formed regions only"
        )
    facts.references.sort(key=lambda r: (r.line, r.relation, r.target or ""))
    return facts


def _error_count(root: Node) -> int:
    count = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_error or node.is_missing:
            count += 1
        stack.extend(node.children)
    return count
