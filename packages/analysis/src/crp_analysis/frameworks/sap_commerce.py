"""SAP Commerce (Hybris) pack: detection, extension modules and configuration-driven relations.

Read as data from the upload, never executed: ``extensioninfo.xml`` and ``localextensions.xml``
(extensions and their dependencies), Spring XML (beans, classes, injected references, parents,
aliases, interceptor mappings), ``*-items.xml`` (item types, inheritance, relations, enums),
ImpEx (header types and ``ServicelayerJob`` ``springId`` rows only) and ``@Resource`` /
``@Qualifier`` annotations in Java (annotation-based injection of beans). Targets defined outside
the upload (SAP platform extensions, beans and types) are ``declared``, never guessed. The
version comes from a CCv2 ``manifest.json`` (``commerceSuiteVersion``) or ``build.number``.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field

from crp_analysis.frameworks import xmlsafe
from crp_analysis.frameworks.base import (
    Capability,
    CapabilityState,
    Mapper,
    PackReport,
    VersionStatus,
    basename,
    java_types,
    line_text,
)
from crp_analysis.graph.manifests import DeclaredDependency, Module
from crp_analysis.graph.resolve import GraphData

ID = "sap-commerce"
NAME = "SAP Commerce"
ADAPTER = "crp-pack-sap-commerce-v1"
SUPPORTED_FAMILIES = ("2105", "2205", "2211")
SUPPORTED_TEXT = (
    "SAP Commerce Cloud 2105, 2205 and 2211 (source-level mapping and rules; version declared "
    "in a CCv2 manifest.json or build.number)"
)
RULES = (
    "crp.sap.flexiblesearch.string-concat",
    "crp.sap.flexiblesearch.unbounded-result",
    "crp.sap.model.save-in-loop",
    "crp.sap.interceptor.persisting-side-effect",
    "crp.sap.cronjob.missing-abort-check",
    "crp.sap.jalo.deprecated-api",
    "crp.java.config.hardcoded-environment-url",
    "crp.java.logging.sensitive-data",
    "crp.sap.extension.dependency-cycle",
)
_OUTSIDE = "defined outside this upload (SAP platform, another repository or generated code)"
_IMPEX_HEADER = re.compile(
    r"^\s*(INSERT_UPDATE|INSERT|UPDATE|REMOVE)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(;.*)?$",
    re.IGNORECASE,
)
_RESOURCE = re.compile(r'@(?:Resource\s*\(\s*name\s*=|Qualifier\s*\()\s*"([^"]+)"')
_VERSION = re.compile(r"^(\d{4})(?:\.(\d+))?$")
_INTERCEPTOR_MAPPING = "InterceptorMapping"


def is_sap_file(path: str) -> bool:
    name = basename(path)
    return (
        name in {"extensioninfo.xml", "localextensions.xml"}
        or name.endswith("-items.xml")
        or (name.endswith(".xml") and "spring" in name)
        or name.endswith(".impex")
        or name in {"manifest.json", "build.number"}
    )


def detected(paths: set[str]) -> bool:
    names = {basename(p) for p in paths}
    return bool(names & {"extensioninfo.xml", "localextensions.xml"}) or any(
        n.endswith("-items.xml") for n in names
    )


def wants(path: str, sap: bool) -> bool:
    """Files the pack reads: metadata always; Java only in detected SAP uploads."""
    return is_sap_file(path) or (sap and path.endswith(".java"))


# -- detection ------------------------------------------------------------------------------------


@dataclass(slots=True)
class Version:
    value: str | None = None
    evidence: str | None = None
    status: str = VersionStatus.UNKNOWN


def detect_version(texts: dict[str, str]) -> Version:
    for path, text in sorted(texts.items()):
        value: str | None = None
        line = 1
        if basename(path) == "manifest.json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            raw = data.get("commerceSuiteVersion") if isinstance(data, dict) else None
            if isinstance(raw, str):
                value = raw.strip()
                line = next(
                    (i for i, t in enumerate(text.splitlines(), 1) if "commerceSuiteVersion" in t),
                    1,
                )
        elif basename(path) == "build.number":
            for number, raw_line in enumerate(text.splitlines(), 1):
                if raw_line.startswith("version="):
                    value, line = raw_line.split("=", 1)[1].strip(), number
                    break
        if not value:
            continue
        match = _VERSION.match(value.split("-", 1)[0])
        if match and match.group(1) in SUPPORTED_FAMILIES:
            status = VersionStatus.SUPPORTED
        else:
            status = VersionStatus.UNSUPPORTED
        return Version(value, f"{path}:{line}", status)
    return Version()


# -- extensions (modules) -------------------------------------------------------------------------


@dataclass(slots=True)
class Extensions:
    modules: list[Module] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    lines: dict[str, int] = field(default_factory=dict)  # extension name -> declaring line
    malformed: int = 0


def parse_extensions(texts: dict[str, str]) -> Extensions:
    found = Extensions()
    for path, text in sorted(texts.items()):
        if basename(path) != "extensioninfo.xml":
            continue
        try:
            root = xmlsafe.parse(text)
        except ValueError as exc:
            found.malformed += 1
            found.notes.append(f"{path}: not parsed ({exc})")
            continue
        extension = root if root.tag == "extension" else root.find("extension")
        name = extension.attrs.get("name") if extension is not None else None
        if extension is None or not name:
            found.malformed += 1
            found.notes.append(f"{path}: no <extension name=...> element")
            continue
        module = Module(posixpath.dirname(path), path, "sap", name)
        for required in extension.findall("requires-extension"):
            target = required.attrs.get("name")
            if target:
                module.dependencies.append(
                    DeclaredDependency(
                        "sap",
                        target,
                        None,
                        "requires-extension",
                        required.line,
                        f"required extension {target} is {_OUTSIDE}",
                    )
                )
        found.lines[name] = extension.line
        found.modules.append(module)
    return found


def dependency_cycles(modules: list[Module]) -> list[list[str]]:
    """Cycles in requires-extension among extensions of this upload (each reported once)."""
    graph = {m.name: [d.name for d in m.dependencies] for m in modules}
    cycles: list[list[str]] = []
    seen: set[frozenset[str]] = set()

    def visit(node: str, trail: list[str]) -> None:
        for target in graph.get(node, []):
            if target not in graph:
                continue
            if target in trail:
                cycle = trail[trail.index(target) :]
                key = frozenset(cycle)
                if key not in seen:
                    seen.add(key)
                    cycles.append(cycle)
                continue
            if len(trail) < 50:
                visit(target, [*trail, target])

    for start in sorted(graph):
        visit(start, [start])
    return cycles


# -- mapping --------------------------------------------------------------------------------------


class _SapMapper:
    def __init__(self, data: GraphData, texts: dict[str, str], extensions: Extensions) -> None:
        self.m = Mapper(data, ADAPTER, "sap")
        self.texts = texts
        self.lines = {path: text.splitlines() for path, text in texts.items()}
        self.extensions = {m.name: m for m in extensions.modules}
        self.types = java_types(data)
        self.beans: dict[str, str] = {}  # bean id or alias -> node key
        self.item_types: dict[str, str] = {}
        self.parsed: dict[str, xmlsafe.Element] = {}
        self.notes: list[str] = list(extensions.notes)
        self.failures: dict[str, int] = {}

    def evidence(self, path: str, line: int | None) -> str | None:
        return line_text(self.lines.get(path, []), line)

    def xml(self, path: str, kind: str) -> xmlsafe.Element | None:
        if path in self.parsed:
            return self.parsed[path]
        try:
            root = xmlsafe.parse(self.texts[path])
        except ValueError as exc:
            self.failures[kind] = self.failures.get(kind, 0) + 1
            self.notes.append(f"{path}: not parsed ({exc})")
            return None
        self.parsed[path] = root
        return root

    def run(self) -> None:
        spring = sorted(p for p in self.texts if p.endswith(".xml") and "spring" in basename(p))
        items = sorted(p for p in self.texts if basename(p).endswith("-items.xml"))
        for path in items:
            self.declare_types(path)
        for path in spring:
            self.declare_beans(path)
        for path in items:
            self.type_relations(path)
        for path in spring:
            self.bean_relations(path)
        for path in sorted(p for p in self.texts if basename(p) == "localextensions.xml"):
            self.local_extensions(path)
        for path in sorted(p for p in self.texts if p.endswith(".impex")):
            self.impex(path)
        for path in sorted(p for p in self.texts if p.endswith(".java")):
            self.annotations(path)

    # item types

    def declare_types(self, path: str) -> None:
        root = self.xml(path, "items")
        if root is None:
            return
        for element in root.iter("itemtype"):
            code = element.attrs.get("code")
            if code:
                deployment = element.find("deployment")
                self.item_types[code] = self.m.component(
                    "itemtype",
                    code,
                    path=path,
                    line=element.line,
                    typecode=deployment.attrs.get("typecode") if deployment else None,
                    table=deployment.attrs.get("table") if deployment else None,
                )
        for element in root.iter("enumtype"):
            code = element.attrs.get("code")
            if code:
                self.item_types[code] = self.m.component(
                    "enumtype", code, path=path, line=element.line
                )

    def type_target(self, code: str) -> tuple[str, str, str]:
        if code in self.item_types:
            return self.item_types[code], "resolved", "item type defined in this upload"
        return (
            self.m.external("sap-type", code, f"{code} (SAP type)"),
            "declared",
            f"item type {code} is {_OUTSIDE}",
        )

    def type_relations(self, path: str) -> None:
        root = self.parsed.get(path)
        if root is None:
            return
        for element in root.iter("itemtype"):
            code, parent = element.attrs.get("code"), element.attrs.get("extends")
            if code and parent and code in self.item_types:
                target, classification, reason = self.type_target(parent)
                self.m.edge(
                    self.item_types[code],
                    target,
                    "extends_type",
                    classification,
                    parent,
                    reason,
                    path=path,
                    line=element.line,
                    text=self.evidence(path, element.line),
                )
        for relation in root.iter("relation"):
            source, target_element = relation.find("sourceElement"), relation.find("targetElement")
            if source is None or target_element is None:
                continue
            source_type, target_type = source.attrs.get("type"), target_element.attrs.get("type")
            if not source_type or not target_type:
                continue
            source_key, _, _ = self.type_target(source_type)
            target, classification, reason = self.type_target(target_type)
            self.m.edge(
                source_key,
                target,
                "relates_to",
                classification,
                target_type,
                f"relation {relation.attrs.get('code', '?')}; {reason}",
                path=path,
                line=relation.line,
                text=self.evidence(path, relation.line),
            )

    # Spring beans

    def declare_beans(self, path: str) -> None:
        root = self.xml(path, "spring")
        if root is None:
            return
        for element in root.iter("bean"):
            bean_id = element.attrs.get("id") or element.attrs.get("name")
            if bean_id and bean_id not in self.beans:
                self.beans[bean_id] = self.m.component(
                    "spring_bean",
                    bean_id,
                    path=path,
                    line=element.line,
                    bean_class=element.attrs.get("class"),
                    parent=element.attrs.get("parent"),
                )
        for element in root.iter("alias"):
            name, alias = element.attrs.get("name"), element.attrs.get("alias")
            if name and alias:
                self.beans.setdefault(
                    alias,
                    self.m.component(
                        "spring_alias", alias, path=path, line=element.line, alias_of=name
                    ),
                )

    def bean_target(self, name: str) -> tuple[str, str, str]:
        if name in self.beans:
            return self.beans[name], "resolved", "bean defined in this upload"
        return (
            self.m.external("sap-bean", name, f"{name} (bean)"),
            "declared",
            f"bean {name} is {_OUTSIDE}",
        )

    def bean_relations(self, path: str) -> None:
        root = self.parsed.get(path)
        if root is None:
            return
        for element in root.iter("alias"):
            name, alias = element.attrs.get("name"), element.attrs.get("alias")
            if name and alias and alias in self.beans:
                target, classification, reason = self.bean_target(name)
                self.m.edge(
                    self.beans[alias],
                    target,
                    "alias_of",
                    classification,
                    name,
                    reason,
                    path=path,
                    line=element.line,
                    text=self.evidence(path, element.line),
                )
        for element in root.iter("bean"):
            bean_id = element.attrs.get("id") or element.attrs.get("name")
            source = self.beans.get(bean_id) if bean_id else None
            if source is None:
                continue
            node = self.m.data.nodes[source]
            if node.path != path or node.start_line != element.line:
                continue  # a later definition with the same id: the first one is mapped
            self.bean_class(source, element, path)
            if parent := element.attrs.get("parent"):
                target, classification, reason = self.bean_target(parent)
                self.m.edge(
                    source,
                    target,
                    "extends_bean",
                    classification,
                    parent,
                    reason,
                    path=path,
                    line=element.line,
                    text=self.evidence(path, element.line),
                )
            for name, value in element.attrs.items():
                if name.endswith("-ref") and value:
                    self.inject(source, value, f"property {name[:-4]}", path, element.line)
            type_code: tuple[str, int] | None = None
            for child in element.children:
                if child.tag not in {"property", "constructor-arg"}:
                    continue
                label = f"{child.tag} {child.attrs.get('name', child.attrs.get('index', ''))}"
                if ref := child.attrs.get("ref"):
                    self.inject(source, ref, label.strip(), path, child.line)
                for nested in child.iter("ref"):
                    if bean := nested.attrs.get("bean"):
                        self.inject(source, bean, label.strip(), path, nested.line)
                if child.attrs.get("name") == "typeCode" and child.attrs.get("value"):
                    type_code = (child.attrs["value"], child.line)
            if type_code and (element.attrs.get("class") or "").endswith(_INTERCEPTOR_MAPPING):
                self.interceptor(element, type_code, path)

    def bean_class(self, source: str, element: xmlsafe.Element, path: str) -> None:
        name = element.attrs.get("class")
        if not name:
            return
        if name in self.types:
            target, classification, reason = self.types[name], "resolved", "class in this upload"
        else:
            target = self.m.external("java", name, f"{name} (class)")
            classification, reason = "declared", f"class {_OUTSIDE}"
        self.m.edge(
            source,
            target,
            "implemented_by",
            classification,
            name,
            reason,
            path=path,
            line=element.line,
            text=self.evidence(path, element.line),
        )

    def inject(self, source: str, ref: str, label: str, path: str, line: int) -> None:
        target, classification, reason = self.bean_target(ref)
        self.m.edge(
            source,
            target,
            "injects",
            classification,
            ref,
            f"{label}; {reason}",
            path=path,
            line=line,
            text=self.evidence(path, line),
        )

    def interceptor(self, element: xmlsafe.Element, type_code: tuple[str, int], path: str) -> None:
        interceptor = next(
            (
                c.attrs.get("ref")
                for c in element.children
                if c.tag == "property" and c.attrs.get("name") == "interceptor"
            ),
            None,
        )
        if not interceptor:
            return
        source, _, _ = self.bean_target(interceptor)
        target, classification, reason = self.type_target(type_code[0])
        self.m.edge(
            source,
            target,
            "intercepts",
            classification,
            type_code[0],
            f"InterceptorMapping {element.attrs.get('id', '')}; {reason}".strip(),
            path=path,
            line=type_code[1],
            text=self.evidence(path, type_code[1]),
        )

    # localextensions.xml, ImpEx, annotations

    def local_extensions(self, path: str) -> None:
        root = self.xml(path, "localextensions")
        if root is None:
            return
        source = self.m.file(path)
        for element in root.iter("extension"):
            name = element.attrs.get("name") or posixpath.basename(
                element.attrs.get("dir", "").rstrip("/")
            )
            if not name:
                continue
            module = self.extensions.get(name)
            if module is not None:
                target, classification, reason = module.key, "resolved", "extension in this upload"
            else:
                target = self.m.external("sap", name)
                classification, reason = "declared", f"extension {_OUTSIDE}"
            self.m.edge(
                source,
                target,
                "loads_extension",
                classification,
                name,
                reason,
                path=path,
                line=element.line,
                text=self.evidence(path, element.line),
            )

    def impex(self, path: str) -> None:
        source = self.m.file(path)
        header: list[str] | None = None
        header_type = ""
        for number, raw in enumerate(self.lines.get(path, []), 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith(("#", "$", '"#')):
                continue
            match = _IMPEX_HEADER.match(stripped)
            if match:
                header_type = match.group(2)
                header = [c.split("[", 1)[0].strip() for c in (match.group(3) or "").split(";")]
                target, classification, reason = self.type_target(header_type)
                self.m.edge(
                    source,
                    target,
                    "imports_data",
                    classification,
                    header_type,
                    f"{match.group(1).upper()} header; {reason}",
                    path=path,
                    line=number,
                    text=self.evidence(path, number),
                )
                continue
            if (
                header
                and header_type.endswith("Job")
                and "springId" in header
                and stripped.startswith(";")
            ):
                cells = [c.strip() for c in stripped.split(";")]
                index = header.index("springId")
                if index < len(cells) and cells[index]:
                    target, classification, reason = self.bean_target(cells[index])
                    self.m.edge(
                        source,
                        target,
                        "runs_bean",
                        classification,
                        cells[index],
                        f"{header_type} springId; {reason}",
                        path=path,
                        line=number,
                        text=self.evidence(path, number),
                    )

    def annotations(self, path: str) -> None:
        if not _RESOURCE.search(self.texts[path]):
            return
        source = self.m.file(path)
        for number, raw in enumerate(self.lines.get(path, []), 1):
            for match in _RESOURCE.finditer(raw):
                self.inject(source, match.group(1), "annotation", path, number)


def map_upload(data: GraphData, texts: dict[str, str], extensions: Extensions) -> PackReport:
    version = detect_version(texts)
    mapper = _SapMapper(data, texts, extensions)
    mapper.run()
    counts = mapper.m.relations
    spring_files = sum(1 for p in texts if p.endswith(".xml") and "spring" in basename(p))
    item_files = sum(1 for p in texts if basename(p).endswith("-items.xml"))
    impex_files = sum(1 for p in texts if p.endswith(".impex"))

    def state(total: int, failed: int) -> str:
        if total == 0:
            return CapabilityState.UNAVAILABLE
        return CapabilityState.PARTIAL if failed else CapabilityState.AVAILABLE

    capabilities = [
        Capability(
            "extensions",
            "Extensions and their dependencies",
            state(len(extensions.modules) + extensions.malformed, extensions.malformed),
            f"{len(extensions.modules)} extensions from extensioninfo.xml; localextensions.xml "
            "entries linked",
        ),
        Capability(
            "spring_wiring",
            "Spring beans and injection",
            state(spring_files, mapper.failures.get("spring", 0)),
            "XML beans, classes, parents, aliases, property/constructor references and "
            "@Resource/@Qualifier annotations; no component scanning or runtime overrides",
        ),
        Capability(
            "item_types",
            "Item types and relations",
            state(item_files, mapper.failures.get("items", 0)),
            "itemtype/enumtype definitions, extends and relation elements",
        ),
        Capability(
            "impex",
            "ImpEx references",
            state(impex_files, 0),
            "header types and ServicelayerJob springId only; ImpEx is never executed",
        ),
        Capability(
            "interceptors",
            "Interceptors",
            CapabilityState.AVAILABLE if counts.get("intercepts") else CapabilityState.PARTIAL,
            "InterceptorMapping beans to item types; interceptor logic is checked by rules",
        ),
        Capability(
            "layers",
            "OCC, facades, services, DAOs, converters and populators",
            CapabilityState.PARTIAL,
            "linked through bean references and injection annotations; no call graph",
        ),
        Capability(
            "processes",
            "Business processes, integrations and CMS",
            CapabilityState.UNAVAILABLE,
            "process definitions and integration objects are not mapped in this version",
        ),
        Capability(
            "build_validation",
            "Build and runtime validation",
            CapabilityState.UNAVAILABLE,
            "needs a licensed SAP Commerce distribution in an authorized environment; not run",
        ),
    ]
    notes = sorted(set(mapper.notes))[:50]
    if version.status == VersionStatus.UNKNOWN:
        notes.insert(
            0,
            "No platform version declared (manifest.json or build.number); results "
            "assume a current release and may not match older versions.",
        )
    elif version.status == VersionStatus.UNSUPPORTED:
        notes.insert(
            0,
            f"Version {version.value} is outside the validated families; mapping "
            "and rules run conservatively and may miss version-specific behaviour.",
        )
    return PackReport(
        id=ID,
        name=NAME,
        adapter=ADAPTER,
        status="experimental",
        version=version.value,
        version_status=version.status,
        version_evidence=version.evidence,
        supported_versions=SUPPORTED_TEXT,
        capabilities=capabilities,
        relations=dict(sorted(counts.items())),
        components=dict(sorted(mapper.m.components.items())),
        rules=list(RULES),
        notes=notes,
    )
