"""Framework pack contracts: detection, capability records and graph mapping helpers.

A pack reads framework metadata from the frozen upload (never executing it), detects the platform
and its version conservatively, maps configuration-driven relations into the snapshot graph with
file/line evidence, and reports per-capability coverage so the UI never implies more support than
exists. Unknown or unsupported versions are disclosed, not guessed.
"""

from __future__ import annotations

import posixpath
from dataclasses import asdict, dataclass, field
from typing import Any

from crp_analysis.graph.resolve import EdgeSpec, GraphData, NodeSpec

MAX_EVIDENCE = 300


class VersionStatus:
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported_version"
    UNKNOWN = "unknown_version"


class CapabilityState:
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(slots=True)
class Capability:
    id: str
    label: str
    state: str
    detail: str


@dataclass(slots=True)
class PackReport:
    """Stored in the graph build's diagnostics (``frameworks``) and shown per upload."""

    id: str
    name: str
    adapter: str
    status: str  # experimental | sme_reviewed (no pack is SME-reviewed yet)
    version: str | None
    version_status: str
    version_evidence: str | None
    supported_versions: str
    capabilities: list[Capability] = field(default_factory=list)
    relations: dict[str, int] = field(default_factory=dict)
    components: dict[str, int] = field(default_factory=dict)
    rules: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def line_text(lines: list[str], line: int | None) -> str | None:
    if line is None or line < 1 or line > len(lines):
        return None
    return " ".join(lines[line - 1].split())[:MAX_EVIDENCE] or None


class Mapper:
    """Adds one pack's nodes and edges to a built graph, counting what it added."""

    def __init__(self, data: GraphData, extractor: str, framework: str) -> None:
        self.data = data
        self.extractor = extractor
        self.framework = framework
        self.relations: dict[str, int] = {}
        self.components: dict[str, int] = {}

    def component(
        self,
        component_type: str,
        name: str,
        *,
        path: str | None = None,
        line: int | None = None,
        label: str | None = None,
        **attributes: object,
    ) -> str:
        key = f"component:{self.framework}:{component_type}:{name}"
        if key not in self.data.nodes:
            self.components[component_type] = self.components.get(component_type, 0) + 1
            module = self.module_of(path)
            self.data.nodes[key] = NodeSpec(
                key,
                "component",
                (label or name)[:512],
                path=path,
                start_line=line,
                end_line=line,
                module_key=module,
                attributes={
                    "framework": self.framework,
                    "component_type": component_type,
                    **{k: v for k, v in attributes.items() if v is not None},
                },
            )
        return key

    def external(self, ecosystem: str, name: str, label: str | None = None) -> str:
        key = f"external:{ecosystem}:{name}"
        if key not in self.data.nodes:
            self.data.nodes[key] = NodeSpec(
                key, "external", (label or name)[:512], attributes={"ecosystem": ecosystem}
            )
        return key

    def module_of(self, path: str | None) -> str | None:
        file_node = self.data.nodes.get(f"file:{path}") if path else None
        return file_node.module_key if file_node is not None else None

    def file(self, path: str) -> str:
        """The file's node; configuration files get one here (sources have one already)."""
        key = f"file:{path}"
        if key not in self.data.nodes:
            self.data.nodes[key] = NodeSpec(
                key,
                "file",
                posixpath.basename(path),
                path=path,
                module_key=self.nearest_module(path),
                attributes={"disposition": "ANALYZABLE", "mapped_by": self.extractor},
            )
        return key

    def nearest_module(self, path: str) -> str | None:
        best: tuple[int, str] | None = None
        for key, node in self.data.nodes.items():
            if node.kind != "module":
                continue
            directory = str(node.attributes.get("directory", ""))
            if directory and not path.startswith(directory + "/"):
                continue
            if best is None or len(directory) > best[0]:
                best = (len(directory), key)
        return best[1] if best else None

    def edge(
        self,
        source: str,
        target: str | None,
        relation: str,
        classification: str,
        target_ref: str,
        reason: str,
        *,
        path: str | None,
        line: int | None,
        text: str | None,
    ) -> None:
        if source not in self.data.nodes:
            return
        if target is not None and target not in self.data.nodes:
            target = None
        if target is None and classification == "resolved":
            classification, reason = "unresolved", f"{reason}; target node missing"
        self.relations[relation] = self.relations.get(relation, 0) + 1
        self.data.edges.append(
            EdgeSpec(
                source,
                target,
                relation,
                classification,
                target_ref[:1024],
                reason,
                path,
                line,
                line,
                text,
                extractor=self.extractor,
            )
        )


def java_types(data: GraphData) -> dict[str, str]:
    """Fully qualified Java type name -> type node key, from the syntax graph."""
    return {
        str(node.attributes["fqn"]): key
        for key, node in data.nodes.items()
        if node.kind == "type" and "fqn" in node.attributes
    }


def basename(path: str) -> str:
    return posixpath.basename(path)
