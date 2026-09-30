"""Secure, line-aware XML reading for framework metadata from untrusted uploads.

Uses expat directly so every element keeps the line it starts on (evidence anchors). Documents
with a DOCTYPE, entity declarations, external entity references or processing of parameter
entities are refused before any content is used, so entity expansion and external fetches are
impossible. Namespaces are reduced to local names. Nothing is executed.
"""

from __future__ import annotations

import xml.parsers.expat as expat
from collections.abc import Iterator
from dataclasses import dataclass, field

MAX_ELEMENTS = 200_000


class UnsafeXmlError(ValueError):
    """The document declares a DTD or entities, or is too large; it is not parsed."""


@dataclass(slots=True)
class Element:
    tag: str
    attrs: dict[str, str]
    line: int
    children: list[Element] = field(default_factory=list)
    text: str = ""

    def iter(self, tag: str | None = None) -> Iterator[Element]:
        if tag is None or self.tag == tag:
            yield self
        for child in self.children:
            yield from child.iter(tag)

    def find(self, tag: str) -> Element | None:
        return next((c for c in self.children if c.tag == tag), None)

    def findall(self, tag: str) -> list[Element]:
        return [c for c in self.children if c.tag == tag]

    def child_text(self, tag: str) -> str | None:
        found = self.find(tag)
        return found.text.strip() or None if found is not None else None


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1].rsplit(" ", 1)[-1].rsplit(":", 1)[-1]


def parse(data: bytes | str) -> Element:
    """Parse ``data``; raises ``UnsafeXmlError`` (DTD/entities/size) or ``ValueError``."""
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack: list[Element] = []
    root: list[Element] = []
    count = 0

    def refuse(*_: object) -> None:
        raise UnsafeXmlError("DTD or entity declarations are not accepted")

    def start(name: str, attrs: dict[str, str]) -> None:
        nonlocal count
        count += 1
        if count > MAX_ELEMENTS:
            raise UnsafeXmlError("too many elements")
        element = Element(
            _local(name), {_local(k): v for k, v in attrs.items()}, parser.CurrentLineNumber
        )
        if stack:
            stack[-1].children.append(element)
        else:
            root.append(element)
        stack.append(element)

    def end(_: str) -> None:
        stack.pop()

    def chars(text: str) -> None:
        if stack:
            stack[-1].text += text

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse  # type: ignore[assignment]
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ValueError(f"malformed XML: {expat.errors.messages.get(exc.code, exc)}") from exc
    if not root:
        raise ValueError("empty XML document")
    return root[0]
