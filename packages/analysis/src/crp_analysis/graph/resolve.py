"""Resolve extracted references into snapshot-scoped nodes and classified edges.

Classifications:

* ``resolved``   - the target was located in this snapshot by a deterministic rule (Java
  fully-qualified name index, Java single-type/same-package/on-demand import rules, relative
  module paths with the TypeScript/Node extension rules, ``tsconfig`` ``paths``/``baseUrl``,
  workspace package names).
* ``declared``   - the target is outside the snapshot but the source statement is consistent
  with a declaration: JDK platform package, Node.js built-in, dependency declared in a manifest.
* ``inferred``   - a heuristic match (Maven groupId prefix to Java package; binding imported
  from a resolved module whose declaration was not found by syntax).
* ``unresolved`` - the target is unknown (missing classpath, undeclared package, dynamic
  specifier, file outside the snapshot); the reason says why.

No Java classpath, compiled output, installed ``node_modules`` or type checker is used, so
call/usage edges and types provided by dependencies are not resolved.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from crp_analysis.graph.extract import Binding, FileFacts, Reference
from crp_analysis.graph.manifests import Module, TsConfig

MAX_NODES = 100_000
MAX_EDGES = 250_000
JAVA_EXTENSIONS = (".java",)
TS_EXTENSIONS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".json")
_INDEX_EXTENSIONS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs")
_TS_FOR_JS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
_JDK_ROOTS = ("java.", "jdk.", "sun.", "com.sun.", "org.w3c.dom", "org.xml.sax", "org.ietf.jgss")
_JDK_JAVAX = (
    "javax.crypto",
    "javax.net",
    "javax.security",
    "javax.sql",
    "javax.naming",
    "javax.management",
    "javax.xml",
    "javax.swing",
    "javax.imageio",
    "javax.sound",
    "javax.print",
    "javax.script",
    "javax.tools",
    "javax.lang.model",
    "javax.annotation.processing",
    "javax.accessibility",
    "javax.rmi",
    "javax.transaction.xa",
)
_JAVA_LANG = frozenset(
    {
        "Object", "String", "Exception", "RuntimeException", "Error", "Throwable", "Thread",
        "Runnable", "Comparable", "Iterable", "AutoCloseable", "Cloneable", "Enum", "Record",
        "Number", "CharSequence", "IllegalArgumentException", "IllegalStateException",
        "UnsupportedOperationException", "NullPointerException", "ClassCastException",
        "IndexOutOfBoundsException", "ArithmeticException", "InterruptedException",
        "CloneNotSupportedException", "ReflectiveOperationException", "SecurityException",
        "Appendable", "Readable", "ThreadLocal", "ClassLoader", "Integer", "Long", "Boolean",
    }
)  # fmt: skip
NODE_BUILTINS = frozenset(
    {
        "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants",
        "crypto", "dgram", "diagnostics_channel", "dns", "domain", "events", "fs", "http",
        "http2", "https", "inspector", "module", "net", "os", "path", "perf_hooks", "process",
        "punycode", "querystring", "readline", "repl", "stream", "string_decoder", "sys",
        "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm", "wasi",
        "worker_threads", "zlib", "test", "sqlite",
    }
)  # fmt: skip
RELATIONS_FOR_IMPACT = (
    "imports",
    "reexports",
    "requires",
    "dynamic_import",
    "extends",
    "implements",
)


@dataclass(slots=True)
class NodeSpec:
    key: str
    kind: str  # GraphNodeKind value
    label: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    module_key: str | None = None
    attributes: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class EdgeSpec:
    source: str
    target: str | None
    relation: str
    classification: str
    target_ref: str
    reason: str
    path: str | None
    start_line: int | None
    end_line: int | None
    text: str | None
    extractor: str | None = None  # None: the syntax extractor; framework packs name themselves


@dataclass(slots=True)
class GraphData:
    nodes: dict[str, NodeSpec] = field(default_factory=dict)
    edges: list[EdgeSpec] = field(default_factory=list)
    diagnostics: dict[str, object] = field(default_factory=dict)
    truncated: bool = False


def _java_package_of(name: str) -> str:
    """Leading lower-case segments (Java convention): ``java.util.Map.Entry`` -> ``java.util``."""
    parts = name.split(".")
    pkg: list[str] = []
    for part in parts:
        if part[:1].isupper():
            break
        pkg.append(part)
    return ".".join(pkg) or name


def _is_jdk(name: str) -> bool:
    return name.startswith(_JDK_ROOTS) or any(
        name == p or name.startswith(p + ".") for p in _JDK_JAVAX
    )


_LWC_MODULE = re.compile(r"^c/([A-Za-z][A-Za-z0-9_]*)$")


def _npm_package(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0]


def _ancestors(path: str) -> list[str]:
    """Directories from the file's own directory up to the root ("")."""
    out: list[str] = []
    current = posixpath.dirname(path)
    while True:
        out.append(current)
        if not current:
            return out
        current = posixpath.dirname(current)


class _Builder:
    def __init__(
        self,
        sources: dict[str, str | None],
        facts: dict[str, FileFacts],
        known_files: dict[str, str],
        modules: list[Module],
        tsconfigs: list[TsConfig],
    ) -> None:
        self.sources = sources
        self.facts = facts
        self.known = known_files
        self.modules = modules
        self.tsconfigs = {t.directory: t for t in tsconfigs}
        self.data = GraphData()
        self.unresolved_reasons: dict[str, int] = {}
        self.by_dir: dict[str, list[Module]] = {}
        for module in modules:
            self.by_dir.setdefault(module.directory, []).append(module)
        self.workspace = {m.name: m for m in modules if m.ecosystem == "npm"}
        self.maven = {f"{m.group_id}:{m.name}": m for m in modules if m.ecosystem == "maven"}
        # Framework modules (SAP extensions, Salesforce package directories) resolve by name.
        self.named = {
            (m.ecosystem, m.name): m for m in modules if m.ecosystem not in {"maven", "npm"}
        }
        self.java_index: dict[str, str] = {}
        self.java_packages: set[str] = set()
        self.root_module: str | None = None

    # -- nodes ---------------------------------------------------------------------------------

    def node(self, spec: NodeSpec) -> str:
        if spec.key not in self.data.nodes:
            if len(self.data.nodes) >= MAX_NODES:
                self.data.truncated = True
                return spec.key
            self.data.nodes[spec.key] = spec
        return spec.key

    def has(self, key: str | None) -> bool:
        return key is not None and key in self.data.nodes

    def module_for(self, path: str, prefer: str | None) -> str:
        for directory in _ancestors(path):
            found = self.by_dir.get(directory)
            if found:
                preferred = [m for m in found if m.ecosystem == prefer] or found
                return preferred[0].key
        if self.root_module is None:
            self.root_module = self.node(
                NodeSpec(
                    "module:root:.",
                    "module",
                    "(snapshot root)",
                    attributes={"synthetic": True, "reason": "no module manifest above file"},
                )
            )
        return self.root_module

    def module_chain(self, path: str, ecosystem: str) -> list[Module]:
        chain: list[Module] = []
        for directory in _ancestors(path):
            chain.extend(m for m in self.by_dir.get(directory, []) if m.ecosystem == ecosystem)
        return chain

    def file_node(self, path: str) -> str:
        language = self.sources.get(path)
        prefer = "maven" if path.endswith(JAVA_EXTENSIONS) else "npm"
        facts = self.facts.get(path)
        attributes: dict[str, object] = {"disposition": self.known.get(path, "UNKNOWN")}
        if language:
            attributes["language"] = language
        if facts is not None:
            attributes["parse_status"] = facts.status
            if facts.truncated:
                attributes["references_truncated"] = True
        return self.node(
            NodeSpec(
                f"file:{path}",
                "file",
                posixpath.basename(path),
                path=path,
                module_key=self.module_for(path, prefer),
                attributes=attributes,
            )
        )

    def external(self, ecosystem: str, name: str, label: str | None = None) -> str:
        return self.node(
            NodeSpec(
                f"external:{ecosystem}:{name}",
                "external",
                label or name,
                attributes={"ecosystem": ecosystem},
            )
        )

    # -- edges ---------------------------------------------------------------------------------

    def edge(
        self,
        source: str,
        target: str | None,
        ref: Reference,
        path: str,
        classification: str,
        reason: str,
    ) -> None:
        self.add_edge(
            EdgeSpec(
                source,
                target if self.has(target) else None,
                ref.relation,
                classification,
                (ref.target or "<non-literal>")[:1024],
                reason,
                path,
                ref.line,
                ref.end_line,
                ref.text or None,
            )
        )

    def add_edge(self, spec: EdgeSpec) -> None:
        if spec.target is None and spec.classification == "resolved":
            spec.classification = "unresolved"
            spec.reason = "target node limit reached"
        if len(self.data.edges) >= MAX_EDGES:
            self.data.truncated = True
            return
        if spec.classification == "unresolved":
            short = spec.reason.split(";")[0][:80]
            self.unresolved_reasons[short] = self.unresolved_reasons.get(short, 0) + 1
        self.data.edges.append(spec)

    # -- build ---------------------------------------------------------------------------------

    def build(self) -> GraphData:
        for module in self.modules:
            self.node(
                NodeSpec(
                    module.key,
                    "module",
                    module.name,
                    path=module.manifest,
                    attributes={
                        "ecosystem": module.ecosystem,
                        "manifest": module.manifest,
                        "directory": module.directory,
                        **({"group_id": module.group_id} if module.group_id else {}),
                        **({"notes": module.notes} if module.notes else {}),
                    },
                )
            )
        for path in sorted(self.sources):
            self.file_node(path)
            facts = self.facts.get(path)
            if facts is None:
                continue
            if path.endswith(JAVA_EXTENSIONS) and facts.package:
                self.java_packages.add(facts.package)
                self.node(NodeSpec(f"package:{facts.package}", "package", facts.package))
            for declared in facts.types:
                key = self.type_key(path, declared.name)
                attributes: dict[str, object] = {"type_kind": declared.kind}
                if declared.abstract:
                    attributes["abstract"] = True
                if path.endswith(JAVA_EXTENSIONS):
                    fqn = f"{facts.package}.{declared.name}" if facts.package else declared.name
                    self.java_index.setdefault(fqn, key)
                    attributes["fqn"] = fqn
                self.node(
                    NodeSpec(
                        key,
                        "type",
                        declared.name,
                        path=path,
                        start_line=declared.line,
                        end_line=declared.end_line,
                        module_key=self.data.nodes[f"file:{path}"].module_key
                        if f"file:{path}" in self.data.nodes
                        else None,
                        attributes=attributes,
                    )
                )
        for module in self.modules:
            self.module_dependencies(module)
        for path in sorted(self.sources):
            facts = self.facts.get(path)
            if facts is None or facts.status in {"FAILED", "UNSUPPORTED"}:
                continue
            if path.endswith(JAVA_EXTENSIONS):
                self.java_file(path, facts)
            else:
                self.script_file(path, facts)
        counts: dict[str, int] = {}
        for e in self.data.edges:
            counts[e.classification] = counts.get(e.classification, 0) + 1
        self.data.diagnostics.update(
            {
                "edges_by_classification": counts,
                "unresolved_reasons": dict(
                    sorted(self.unresolved_reasons.items(), key=lambda kv: -kv[1])[:20]
                ),
                "truncated": self.data.truncated,
            }
        )
        return self.data

    @staticmethod
    def type_key(path: str, name: str) -> str:
        return f"type:{path}#{name}"

    # -- manifests -----------------------------------------------------------------------------

    def module_dependencies(self, module: Module) -> None:
        for dep in module.dependencies:
            if dep.ecosystem == "maven":
                target_module = self.maven.get(dep.name)
            elif dep.ecosystem == "npm":
                target_module = self.workspace.get(dep.name)
            else:
                target_module = self.named.get((dep.ecosystem, dep.name))
            if target_module is not None and target_module.key != module.key:
                target, classification = target_module.key, "resolved"
                reason = "dependency is a module in this snapshot"
            else:
                target = self.external(dep.ecosystem, dep.name)
                classification = "declared"
                reason = dep.reason or (
                    f"declared {dep.ecosystem} dependency"
                    + (f" {dep.version}" if dep.version else "")
                    + "; not downloaded or version-resolved"
                )
            self.add_edge(
                EdgeSpec(
                    module.key,
                    target,
                    "depends_on",
                    classification,
                    dep.name,
                    reason,
                    module.manifest,
                    dep.line,
                    dep.line,
                    None,
                )
            )

    # -- Java ----------------------------------------------------------------------------------

    def java_import_target(self, name: str, static: bool, path: str) -> tuple[str | None, str, str]:
        if name in self.java_index:
            return self.java_index[name], "resolved", "type declared in this snapshot"
        if static and "." in name and name.rsplit(".", 1)[0] in self.java_index:
            return (
                self.java_index[name.rsplit(".", 1)[0]],
                "resolved",
                "static member of a type in this snapshot",
            )
        package = _java_package_of(name)
        if _is_jdk(name):
            return (
                self.external("jdk", package, f"{package} (JDK)"),
                "declared",
                "JDK platform package (no module path verified)",
            )
        if package in self.java_packages:
            return (
                None,
                "unresolved",
                f"package {package} is in this snapshot but no such type was found",
            )
        for module in self.module_chain(path, "maven"):
            for dep in module.dependencies:
                group = dep.name.split(":", 1)[0]
                if name == group or name.startswith(group + "."):
                    return (
                        self.external("maven", dep.name),
                        "inferred",
                        f"package prefix matches declared Maven dependency {dep.name} "
                        "(no classpath to verify)",
                    )
        target = self.external("java", package, f"{package} (unknown)")
        if not self.modules or not self.module_chain(path, "maven"):
            return (
                target,
                "unresolved",
                "not in snapshot; no Maven manifest declares a matching dependency "
                "(missing classpath)",
            )
        return (
            target,
            "unresolved",
            "not in snapshot and no declared dependency matches (missing classpath)",
        )

    def java_file(self, path: str, facts: FileFacts) -> None:
        source = f"file:{path}"
        single: dict[str, str] = {}
        wildcards: list[str] = []
        for ref in facts.references:
            if ref.relation != "imports" or ref.target is None:
                continue
            if ref.wildcard:
                wildcards.append(ref.target)
                if ref.target in self.java_packages:
                    self.edge(
                        source,
                        f"package:{ref.target}",
                        ref,
                        path,
                        "resolved",
                        "on-demand import of a package in this snapshot",
                    )
                elif ref.target in self.java_index:
                    self.edge(
                        source,
                        self.java_index[ref.target],
                        ref,
                        path,
                        "resolved",
                        "on-demand import of nested types of a type in this snapshot",
                    )
                else:
                    target, classification, reason = self.java_import_target(
                        ref.target + ".*", False, path
                    )
                    self.edge(source, target, ref, path, classification, reason)
                continue
            if not ref.static:
                single[ref.target.rsplit(".", 1)[-1]] = ref.target
            target, classification, reason = self.java_import_target(ref.target, ref.static, path)
            self.edge(source, target, ref, path, classification, reason)
        own = {t.name for t in facts.types}
        for ref in facts.references:
            if ref.relation not in {"extends", "implements"} or ref.target is None:
                continue
            owner = self.type_key(path, ref.owner) if ref.owner else source
            target, classification, reason = self.java_type(
                ref.target, facts, own, single, wildcards, path
            )
            self.edge(owner, target, ref, path, classification, reason)

    def java_type(
        self,
        name: str,
        facts: FileFacts,
        own: set[str],
        single: dict[str, str],
        wildcards: list[str],
        path: str,
    ) -> tuple[str | None, str, str]:
        head = name.split(".", 1)[0]
        if name in own:
            return self.type_key(path, name), "resolved", "declared in the same file"
        if head in single:
            fqn = single[head] + name[len(head) :]
            if fqn in self.java_index:
                return self.java_index[fqn], "resolved", "single-type import"
            return self.java_import_target(single[head], False, path)
        if facts.package and f"{facts.package}.{name}" in self.java_index:
            return self.java_index[f"{facts.package}.{name}"], "resolved", "same package"
        if "." in name:  # qualified name: same rules as an import of that name
            return self.java_import_target(name, False, path)
        if not facts.package and name in self.java_index:
            return self.java_index[name], "resolved", "default package"
        matches = [f"{w}.{name}" for w in wildcards if f"{w}.{name}" in self.java_index]
        if len(matches) == 1:
            return self.java_index[matches[0]], "resolved", "on-demand import"
        if len(matches) > 1:
            return None, "unresolved", f"ambiguous: {len(matches)} on-demand imports provide {name}"
        if name in _JAVA_LANG:
            return (
                self.external("jdk", "java.lang", "java.lang (JDK)"),
                "declared",
                "implicitly imported java.lang type",
            )
        external = [w for w in wildcards if w not in self.java_packages]
        if external:
            return (
                None,
                "unresolved",
                f"may come from on-demand import of {', '.join(external[:3])} (no classpath)",
            )
        return (
            None,
            "unresolved",
            "type not found in this snapshot, same package or imports (missing classpath?)",
        )

    # -- JavaScript / TypeScript ---------------------------------------------------------------

    def find_file(self, base: str) -> str | None:
        base = posixpath.normpath(base)
        if base.startswith("../") or base == "..":
            return None
        if base in self.known:
            return base
        stem, ext = posixpath.splitext(base)
        for replacement in _TS_FOR_JS.get(ext, ()):
            if stem + replacement in self.known:
                return stem + replacement
        for extension in TS_EXTENSIONS:
            if base + extension in self.known:
                return base + extension
        for extension in _INDEX_EXTENSIONS:
            candidate = posixpath.join(base, "index" + extension)
            if candidate in self.known:
                return candidate
        return None

    def tsconfig_for(self, path: str) -> TsConfig | None:
        for directory in _ancestors(path):
            if directory in self.tsconfigs:
                return self.tsconfigs[directory]
        return None

    def script_target(self, spec: str, path: str) -> tuple[str | None, str, str]:
        if spec.startswith(("./", "../")) or spec in {".", ".."}:
            base = posixpath.join(posixpath.dirname(path), spec)
            if posixpath.normpath(base).startswith(".."):
                return None, "unresolved", "relative path points outside the snapshot"
            found = self.find_file(base)
            if found is not None:
                return self.file_node(found), "resolved", "relative module path"
            return None, "unresolved", "relative target not found in this snapshot"
        lwc = self.lwc_target(spec, path)
        if lwc is not None:
            return lwc
        if spec.startswith("node:") or _npm_package(spec) in NODE_BUILTINS:
            name = spec.removeprefix("node:").split("/", 1)[0]
            return (
                self.external("node", name, f"node:{name}"),
                "declared",
                "Node.js built-in module",
            )
        config = self.tsconfig_for(path)
        if config is not None:
            found, reason, matched = self.tsconfig_target(spec, config)
            if found is not None:
                return self.file_node(found), "resolved", reason
            if matched:
                return None, "unresolved", reason
        package = _npm_package(spec)
        if package in self.workspace:
            return self.workspace[package].key, "resolved", "workspace package in this snapshot"
        chain = self.module_chain(path, "npm")
        for module in chain:
            for dep in module.dependencies:
                if dep.name == package:
                    return (
                        self.external("npm", package),
                        "declared",
                        f"declared in {module.manifest} ({dep.scope}); "
                        "not installed or version-resolved",
                    )
        target = self.external("npm", package)
        if not chain:
            return target, "unresolved", "bare package with no package.json above the file"
        return target, "unresolved", "package not declared in any package.json above the file"

    def lwc_target(self, spec: str, path: str) -> tuple[str | None, str, str] | None:
        """Salesforce Lightning Web Components import sibling bundles as ``c/<name>``: the
        bundle's main module ``lwc/<name>/<name>.js`` (or ``.ts``) next to the importing one;
        otherwise a single bundle of that name anywhere in the snapshot."""
        match = _LWC_MODULE.match(spec)
        parts = path.split("/")
        if match is None or "lwc" not in parts[:-1]:
            return None
        name = match.group(1)
        root = "/".join(parts[: len(parts) - 1 - parts[-2::-1].index("lwc")])
        for extension in (".js", ".ts"):
            candidate = f"{root}/{name}/{name}{extension}"
            if candidate in self.known:
                return self.file_node(candidate), "resolved", "Salesforce LWC module (same folder)"
        others = sorted(
            p
            for p in self.known
            if p.endswith((f"/lwc/{name}/{name}.js", f"/lwc/{name}/{name}.ts"))
        )
        if len(others) == 1:
            return self.file_node(others[0]), "inferred", "Salesforce LWC module (another folder)"
        reason = (
            "LWC module not found in this snapshot" if not others else "LWC module is ambiguous"
        )
        return None, "unresolved", reason

    def tsconfig_target(self, spec: str, config: TsConfig) -> tuple[str | None, str, bool]:
        root = config.base_url if config.base_url is not None else config.directory
        for pattern, targets in config.paths.items():
            if "*" in pattern:
                prefix, _, suffix = pattern.partition("*")
                if not (spec.startswith(prefix) and spec.endswith(suffix)) or len(spec) < len(
                    prefix
                ) + len(suffix):
                    continue
                star = spec[len(prefix) : len(spec) - len(suffix)]
            elif spec == pattern:
                star = ""
            else:
                continue
            for target in targets:
                found = self.find_file(posixpath.join(root, target.replace("*", star)))
                if found is not None:
                    return found, f"tsconfig paths '{pattern}' in {config.path}", True
            return None, f"tsconfig paths '{pattern}' matched but no target file exists", True
        if config.base_url is not None:
            found = self.find_file(posixpath.join(config.base_url, spec))
            if found is not None:
                return found, f"tsconfig baseUrl in {config.path}", True
        return None, "", False

    def script_file(self, path: str, facts: FileFacts) -> None:
        source = f"file:{path}"
        resolved_specs: dict[str, tuple[str | None, str]] = {}
        for ref in facts.references:
            if ref.relation not in {"imports", "reexports", "requires", "dynamic_import"}:
                continue
            if ref.target is None:
                self.edge(
                    source,
                    None,
                    ref,
                    path,
                    "unresolved",
                    "dynamic specifier (non-literal); cannot be resolved statically",
                )
                continue
            target, classification, reason = self.script_target(ref.target, path)
            resolved_specs[ref.target] = (target, classification)
            self.edge(source, target, ref, path, classification, reason)
        own = {t.name for t in facts.types}
        bindings = {b.local: b for b in facts.bindings}
        for ref in facts.references:
            if ref.relation not in {"extends", "implements"}:
                continue
            owner = self.type_key(path, ref.owner) if ref.owner else source
            if ref.target is None:
                self.edge(
                    owner,
                    None,
                    ref,
                    path,
                    "unresolved",
                    "heritage expression is not a plain identifier (call/mixin)",
                )
                continue
            target, classification, reason = self.script_type(
                ref.target, path, own, bindings, resolved_specs
            )
            self.edge(owner, target, ref, path, classification, reason)

    def script_type(
        self,
        name: str,
        path: str,
        own: set[str],
        bindings: dict[str, Binding],
        resolved_specs: dict[str, tuple[str | None, str]],
    ) -> tuple[str | None, str, str]:
        if name in own:
            return self.type_key(path, name), "resolved", "declared in the same file"
        head, _, member = name.partition(".")
        binding = bindings.get(head)
        if binding is None:
            return (
                None,
                "unresolved",
                "identifier is neither declared in the file nor imported (global or dynamic)",
            )
        target, classification = resolved_specs.get(binding.specifier, (None, "unresolved"))
        wanted = member if binding.imported == "*" else binding.imported
        if target is not None and target.startswith("file:"):
            target_path = target.removeprefix("file:")
            facts = self.facts.get(target_path)
            if (
                facts is not None
                and wanted
                and wanted not in {"default", "*"}
                and any(t.name == wanted for t in facts.types)
            ):
                return (
                    self.type_key(target_path, wanted),
                    "resolved",
                    f"imported from {binding.specifier}",
                )
            return (
                target,
                "inferred",
                f"binding imported from {binding.specifier}; declaration not identified by "
                "syntax (default export or re-export)",
            )
        if target is not None:
            return target, classification, f"imported from {binding.specifier}"
        return None, "unresolved", f"imported from unresolved module {binding.specifier}"


def build_graph(
    *,
    sources: dict[str, str | None],
    facts: dict[str, FileFacts],
    known_files: dict[str, str],
    modules: Iterable[Module],
    tsconfigs: Iterable[TsConfig],
) -> GraphData:
    """Build nodes and edges. ``sources`` maps graph source files to languages; ``known_files``
    maps every snapshot manifest path to its disposition (targets must be in the snapshot)."""
    return _Builder(sources, facts, known_files, list(modules), list(tsconfigs)).build()
