"""Syntax-level structure extraction with Tree-sitter (no type or cross-file resolution).

Parsing never executes code. Each file yields bounded symbols with exact spans plus a parse
status: OK, PARTIAL (the tree contains ERROR/MISSING nodes) or FAILED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from importlib.metadata import version

import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

EXTRACTOR = "tree-sitter"


def extractor_version() -> str:
    return (
        f"tree-sitter {version('tree-sitter')} (java {version('tree-sitter-java')}, "
        f"javascript {version('tree-sitter-javascript')}, "
        f"typescript {version('tree-sitter-typescript')})"
    )


@cache
def _language(grammar: str) -> Language:
    match grammar:
        case "java":
            return Language(tree_sitter_java.language())
        case "javascript":
            return Language(tree_sitter_javascript.language())
        case "typescript":
            return Language(tree_sitter_typescript.language_typescript())
        case "tsx":
            return Language(tree_sitter_typescript.language_tsx())
    raise ValueError(f"no grammar for {grammar}")


def grammar_for(path: str, language: str | None) -> str | None:
    if language == "java":
        return "java"
    if language == "javascript":
        return "javascript"  # the JavaScript grammar also parses JSX
    if language == "typescript":
        return "tsx" if path.endswith(".tsx") else "typescript"
    return None


@dataclass(frozen=True, slots=True)
class ExtractedSymbol:
    kind: str
    name: str
    container: str | None
    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(slots=True)
class StructureResult:
    status: str
    error_count: int
    symbols: list[ExtractedSymbol] = field(default_factory=list)
    truncated: bool = False
    message: str | None = None


_JAVA_TYPES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
_JAVA_MEMBERS = {"method_declaration": "method", "constructor_declaration": "constructor"}
_JS_TYPES = {
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "type_alias_declaration": "type",
}
_JS_FUNCTIONS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "method_definition": "method",
}
_FUNCTION_VALUES = {"arrow_function", "function_expression", "function"}


def _name(node: Node) -> str | None:
    child = node.child_by_field_name("name")
    if child is None or child.text is None:
        return None
    return child.text.decode("utf-8", errors="replace")[:512]


def _symbol(node: Node, kind: str, name: str, container: str | None) -> ExtractedSymbol:
    return ExtractedSymbol(
        kind,
        name,
        container,
        node.start_point[0] + 1,
        node.start_point[1] + 1,
        node.end_point[0] + 1,
        node.end_point[1] + 1,
    )


def extract(source: bytes, path: str, language: str | None, *, max_symbols: int) -> StructureResult:
    grammar = grammar_for(path, language)
    if grammar is None:
        return StructureResult("UNSUPPORTED", 0, message="no grammar for this language")
    try:
        tree = Parser(_language(grammar)).parse(source)
    except (ValueError, RuntimeError) as exc:
        return StructureResult("FAILED", 0, message=f"parser error: {type(exc).__name__}")

    java = grammar == "java"
    types = _JAVA_TYPES if java else _JS_TYPES
    functions = _JAVA_MEMBERS if java else _JS_FUNCTIONS
    result = StructureResult("OK", 0)
    stack: list[tuple[Node, str | None]] = [(tree.root_node, None)]
    while stack:
        node, container = stack.pop()
        if node.is_error or node.is_missing:
            result.error_count += 1
        kind = node.type
        next_container = container
        found: ExtractedSymbol | None = None
        if kind in types and (name := _name(node)):
            found = _symbol(node, types[kind], name, container)
            next_container = name
        elif kind in functions and (name := _name(node)):
            found = _symbol(node, functions[kind], name, container)
        elif kind in {"import_declaration", "import_statement"} and node.text is not None:
            text = node.text.decode("utf-8", errors="replace").strip().rstrip(";")[:512]
            found = _symbol(node, "import", text, None)
        elif not java and kind == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is not None and value.type in _FUNCTION_VALUES and (name := _name(node)):
                found = _symbol(node, "function", name, container)
        if found is not None:
            if len(result.symbols) >= max_symbols:
                result.truncated = True
            else:
                result.symbols.append(found)
        stack.extend((child, next_container) for child in reversed(node.children))
    if tree.root_node.has_error:
        result.status = "PARTIAL"
        result.message = f"{max(result.error_count, 1)} syntax error node(s)"
    result.symbols.sort(key=lambda s: (s.start_line, s.start_column))
    return result
