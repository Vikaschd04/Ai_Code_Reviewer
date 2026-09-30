"""Salesforce pack: detection, package directories and metadata-driven relations.

Read as data from the upload, never deployed or executed: ``sfdx-project.json`` (package
directories, dependencies, ``sourceApiVersion``), Apex classes and triggers (declarations,
sharing mode, async/entry-point markers, static SOQL ``FROM`` objects; comments and strings are
masked first), Lightning Web Components (``@salesforce/apex`` and ``@salesforce/schema``
imports), object and field metadata (including lookups and org-wide sharing), Flows (trigger
object, record operations, Apex actions), permission sets (object and class access) and custom
metadata records. Objects without metadata in the upload are ``declared`` (standard objects) or
``unresolved`` (custom objects); nothing is inferred from an org because none is connected.
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
    line_text,
)
from crp_analysis.graph.manifests import DeclaredDependency, Module
from crp_analysis.graph.resolve import GraphData

ID = "salesforce"
NAME = "Salesforce"
ADAPTER = "crp-pack-salesforce-v1"
RETIRED_MAX = 30.0  # Platform API versions 21.0-30.0 retired in Summer '25 (Help 000389618)
SUPPORTED_TEXT = (
    "Salesforce DX source format, API versions 31.0 and later (source-level mapping and rules; "
    "API versions 30.0 and earlier are retired and flagged)"
)
RULES = (
    "OperationWithLimitsInLoop",
    "OperationWithHighCostInLoop",
    "AvoidNonRestrictiveQueries",
    "ApexCRUDViolation",
    "ApexSharingViolations",
    "ApexSOQLInjection",
    "ApexSuggestUsingNamedCred",
    "ApexInsecureEndpoint",
    "QueueableWithoutFinalizer",
    "ApexUnitTestShouldNotUseSeeAllDataTrue",
    "ApexUnitTestClassShouldHaveAsserts",
    "crp.sf.metadata.retired-api-version",
)
STANDARD_OBJECTS = frozenset(
    {
        "Account", "Contact", "Lead", "Opportunity", "OpportunityLineItem", "Case", "User",
        "Task", "Event", "Campaign", "CampaignMember", "Contract", "Order", "OrderItem",
        "Product2", "Pricebook2", "PricebookEntry", "Quote", "QuoteLineItem", "Asset",
        "ContentVersion", "ContentDocument", "ContentDocumentLink", "Attachment", "Note",
        "Group", "GroupMember", "Profile", "PermissionSet", "PermissionSetAssignment",
        "RecordType", "Organization", "EmailMessage", "Entitlement", "ServiceContract",
        "WorkOrder", "Individual", "AsyncApexJob", "CronTrigger", "ApexClass", "FeedItem",
    }
)  # fmt: skip
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_STRING = re.compile(r"'(?:\\.|[^'\\\n])*'")
_CLASS = re.compile(
    r"^\s*((?:@\w+(?:\([^)]*\))?\s+)*)"
    r"(?:(?:public|global|private|protected)\s+)?"
    r"(?:(with|without|inherited)\s+sharing\s+)?"
    r"(?:(?:virtual|abstract)\s+)*class\s+(\w+)([^{]*)\{",
    re.I | re.M,
)
_TRIGGER = re.compile(r"\btrigger\s+(\w+)\s+on\s+(\w+)\s*\(([^)]*)\)", re.I)
_SOQL = re.compile(r"\[\s*SELECT\b(?:[^\[\]]|\[[^\]]*\])*?\bFROM\s+(\w+)", re.I)
_DYNAMIC = re.compile(r"\bDatabase\s*\.\s*(?:query|queryWithBinds|countQuery)\s*\(", re.I)
_MARKERS = {
    "future": re.compile(r"@future\b", re.I),
    "callout": re.compile(r"@future\s*\(\s*callout\s*=\s*true|\bHttpRequest\b|\bHttp\s*\(\)", re.I),
    "aura_enabled": re.compile(r"@AuraEnabled\b", re.I),
    "invocable": re.compile(r"@InvocableMethod\b", re.I),
    "rest_resource": re.compile(r"@RestResource\b", re.I),
    "batchable": re.compile(r"\bDatabase\s*\.\s*Batchable\b", re.I),
    "queueable": re.compile(r"\bQueueable\b", re.I),
    "schedulable": re.compile(r"\bSchedulable\b", re.I),
}
_LWC_APEX = re.compile(r"""from\s+['"]@salesforce/apex/(?:(\w+)\.)?(\w+)\.(\w+)['"]""")
_LWC_SCHEMA = re.compile(r"""from\s+['"]@salesforce/schema/(\w+)(?:\.(\w+))?['"]""")
_API_VERSION = re.compile(r"^\d{1,3}\.\d$")


def is_salesforce_file(path: str) -> bool:
    name = basename(path)
    return (
        name == "sfdx-project.json"
        or name.endswith((".cls", ".trigger", "-meta.xml"))
        or ("/lwc/" in f"/{path}" and name.endswith((".js", ".ts")))
    )


def detected(paths: set[str]) -> bool:
    return any(
        basename(p) == "sfdx-project.json" or p.endswith((".cls", ".trigger")) for p in paths
    )


def wants(path: str, salesforce: bool) -> bool:
    return salesforce and is_salesforce_file(path)


def _mask(text: str) -> str:
    """Blank out comments and string literals, keeping offsets and line breaks."""

    def blank(match: re.Match[str]) -> str:
        return "".join("\n" if c == "\n" else " " for c in match.group(0))

    return _STRING.sub(blank, _COMMENT.sub(blank, text))


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def api_version(value: str | None) -> float | None:
    if value is None or not _API_VERSION.match(value.strip()):
        return None
    return float(value.strip())


# -- detection and package directories -------------------------------------------------------------


@dataclass(slots=True)
class Project:
    modules: list[Module] = field(default_factory=list)
    source_api_version: str | None = None
    version_evidence: str | None = None
    notes: list[str] = field(default_factory=list)
    malformed: int = 0


def parse_project(texts: dict[str, str]) -> Project:
    project = Project()
    for path, text in sorted(texts.items()):
        if basename(path) != "sfdx-project.json":
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            project.malformed += 1
            project.notes.append(f"{path}: not parsed ({exc.msg})")
            continue
        if not isinstance(data, dict):
            project.malformed += 1
            project.notes.append(f"{path}: not a JSON object")
            continue
        lines = text.splitlines()
        base = posixpath.dirname(path)
        version = data.get("sourceApiVersion")
        if isinstance(version, str) and project.source_api_version is None:
            number = next((i for i, t in enumerate(lines, 1) if "sourceApiVersion" in t), 1)
            project.source_api_version = version
            project.version_evidence = f"{path}:{number}"
        for entry in data.get("packageDirectories") or []:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            directory = posixpath.normpath(posixpath.join(base, entry["path"]))
            if directory.startswith(".."):
                project.notes.append(f"{path}: package directory outside the upload ignored")
                continue
            name = entry.get("package") if isinstance(entry.get("package"), str) else None
            module = Module("" if directory == "." else directory, path, "sfdx", name or directory)
            for dependency in entry.get("dependencies") or []:
                if isinstance(dependency, dict) and isinstance(dependency.get("package"), str):
                    target = dependency["package"].split("@", 1)[0]
                    module.dependencies.append(
                        DeclaredDependency(
                            "sfdx",
                            target,
                            dependency.get("versionNumber"),
                            "package dependency",
                            next((i for i, t in enumerate(lines, 1) if target in t), None),
                            f"package {target} is not in this upload (installed in the org)",
                        )
                    )
            project.modules.append(module)
    return project


# -- mapping --------------------------------------------------------------------------------------


class _SalesforceMapper:
    def __init__(self, data: GraphData, texts: dict[str, str]) -> None:
        self.m = Mapper(data, ADAPTER, "sf")
        self.texts = texts
        self.lines = {p: t.splitlines() for p, t in texts.items()}
        self.objects: dict[str, str] = {}  # lower-case API name -> node
        self.fields: dict[str, str] = {}  # "object.field" lower-case -> node
        self.classes: dict[str, str] = {}  # lower-case class name -> node
        self.notes: list[str] = []
        self.failures: dict[str, int] = {}
        self.dynamic_queries = 0

    def evidence(self, path: str, line: int | None) -> str | None:
        return line_text(self.lines.get(path, []), line)

    def xml(self, path: str, kind: str) -> xmlsafe.Element | None:
        try:
            return xmlsafe.parse(self.texts[path])
        except ValueError as exc:
            self.failures[kind] = self.failures.get(kind, 0) + 1
            self.notes.append(f"{path}: not parsed ({exc})")
            return None

    def run(self) -> None:
        paths = sorted(self.texts)
        for path in paths:
            if path.endswith(".object-meta.xml"):
                self.declare_object(path)
        for path in paths:
            if path.endswith(".field-meta.xml"):
                self.declare_field(path)
        for path in paths:
            if path.endswith(".cls"):
                self.apex_class(path)
        for path in paths:
            if path.endswith(".trigger"):
                self.apex_trigger(path)
        for path in paths:
            if path.endswith(".cls"):
                self.apex_queries(path, self.classes.get(self.class_name(path).lower()))
            elif "/lwc/" in f"/{path}" and path.endswith((".js", ".ts")):
                self.lwc(path)
            elif path.endswith(".flow-meta.xml"):
                self.flow(path)
            elif path.endswith(".permissionset-meta.xml"):
                self.permission_set(path)
            elif path.endswith(".md-meta.xml"):
                self.custom_metadata_record(path)

    @staticmethod
    def class_name(path: str) -> str:
        return basename(path).rsplit(".", 1)[0]

    # objects and fields

    def declare_object(self, path: str) -> None:
        name = basename(path).removesuffix(".object-meta.xml")
        root = self.xml(path, "objects")
        sharing = root.child_text("sharingModel") if root is not None else None
        self.objects[name.lower()] = self.m.component(
            "sobject",
            name,
            path=path,
            line=root.line if root is not None else 1,
            custom=name.endswith("__c"),
            custom_metadata=name.endswith("__mdt"),
            sharing_model=sharing,
        )

    def object_target(self, name: str) -> tuple[str | None, str, str]:
        if name.lower() in self.objects:
            return self.objects[name.lower()], "resolved", "object metadata in this upload"
        if name in STANDARD_OBJECTS:
            return (
                self.m.external("sf-object", name, f"{name} (standard object)"),
                "declared",
                "standard object; no metadata in the upload",
            )
        if "__" in name:
            return None, "unresolved", "custom object metadata is not in this upload"
        return (
            self.m.external("sf-object", name, f"{name} (object)"),
            "declared",
            "assumed standard object; not verified without org metadata",
        )

    def declare_field(self, path: str) -> None:
        parts = path.split("/")
        if len(parts) < 3 or parts[-2] != "fields":
            return
        obj = parts[-3]
        name = basename(path).removesuffix(".field-meta.xml")
        root = self.xml(path, "fields")
        key = self.m.component(
            "field",
            f"{obj}.{name}",
            path=path,
            line=root.line if root is not None else 1,
            label=f"{obj}.{name}",
            field_type=root.child_text("type") if root is not None else None,
        )
        self.fields[f"{obj}.{name}".lower()] = key
        target, classification, reason = self.object_target(obj)
        self.m.edge(
            key, target, "field_of", classification, obj, reason, path=path, line=1, text=None
        )
        if root is not None and (reference := root.find("referenceTo")) is not None:
            referenced = reference.text.strip()
            if referenced:
                target, classification, reason = self.object_target(referenced)
                self.m.edge(
                    key,
                    target,
                    "lookup_to",
                    classification,
                    referenced,
                    reason,
                    path=path,
                    line=reference.line,
                    text=self.evidence(path, reference.line),
                )

    # Apex

    def apex_class(self, path: str) -> None:
        masked = _mask(self.texts[path])
        match = _CLASS.search(masked)
        name = match.group(3) if match else self.class_name(path)
        header = (match.group(1) + " " + match.group(4)) if match else ""
        markers = sorted(k for k, pattern in _MARKERS.items() if pattern.search(masked))
        self.classes[name.lower()] = self.m.component(
            "apex_class",
            name,
            path=path,
            line=_line(masked, match.start(3)) if match else 1,
            sharing=(match.group(2).lower() + " sharing")
            if match and match.group(2)
            else "not declared",
            is_test=bool(re.search(r"@isTest\b", header, re.I)),
            entry_points=markers or None,
        )

    def apex_trigger(self, path: str) -> None:
        masked = _mask(self.texts[path])
        match = _TRIGGER.search(masked)
        if match is None:
            self.failures["apex"] = self.failures.get("apex", 0) + 1
            self.notes.append(f"{path}: trigger declaration not recognised")
            return
        line = _line(masked, match.start())
        events = ", ".join(e.strip().lower() for e in match.group(3).split(",") if e.strip())
        key = self.m.component("apex_trigger", match.group(1), path=path, line=line, events=events)
        target, classification, reason = self.object_target(match.group(2))
        self.m.edge(
            key,
            target,
            "triggers_on",
            classification,
            match.group(2),
            f"{events}; {reason}",
            path=path,
            line=line,
            text=self.evidence(path, line),
        )
        self.apex_queries(path, key)

    def apex_queries(self, path: str, source: str | None) -> None:
        if source is None:
            return
        masked = _mask(self.texts[path])
        for match in _SOQL.finditer(masked):
            line = _line(masked, match.start(1))
            target, classification, reason = self.object_target(match.group(1))
            self.m.edge(
                source,
                target,
                "queries",
                classification,
                match.group(1),
                f"static SOQL; {reason}",
                path=path,
                line=line,
                text=self.evidence(path, line),
            )
        self.dynamic_queries += len(_DYNAMIC.findall(masked))

    def class_target(self, name: str, namespace: str | None) -> tuple[str | None, str, str]:
        if namespace is None and name.lower() in self.classes:
            return self.classes[name.lower()], "resolved", "Apex class in this upload"
        label = f"{namespace}.{name}" if namespace else name
        return (
            self.m.external("sf-apex", label.lower(), f"{label} (Apex)"),
            "declared",
            "Apex class not in this upload (managed package or another repository)",
        )

    # Lightning Web Components

    def lwc(self, path: str) -> None:
        parts = path.split("/")
        index = parts.index("lwc") if "lwc" in parts else -1
        if index < 0 or index + 1 >= len(parts) - 1:
            return
        bundle = parts[index + 1]
        key = self.m.component("lwc", bundle, path=path, line=1)
        text = self.texts[path]
        for match in _LWC_APEX.finditer(text):
            line = _line(text, match.start())
            target, classification, reason = self.class_target(match.group(2), match.group(1))
            self.m.edge(
                key,
                target,
                "calls_apex",
                classification,
                f"{match.group(2)}.{match.group(3)}",
                f"@salesforce/apex import; {reason}",
                path=path,
                line=line,
                text=self.evidence(path, line),
            )
        for match in _LWC_SCHEMA.finditer(text):
            line = _line(text, match.start())
            obj, fld = match.group(1), match.group(2)
            if fld and f"{obj}.{fld}".lower() in self.fields:
                target, classification, reason = (
                    self.fields[f"{obj}.{fld}".lower()],
                    "resolved",
                    "field metadata in this upload",
                )
            else:
                target, classification, reason = self.object_target(obj)
            self.m.edge(
                key,
                target,
                "references_schema",
                classification,
                f"{obj}.{fld}" if fld else obj,
                f"@salesforce/schema import; {reason}",
                path=path,
                line=line,
                text=self.evidence(path, line),
            )

    # Flows, permission sets, custom metadata

    def flow(self, path: str) -> None:
        root = self.xml(path, "flows")
        if root is None:
            return
        name = basename(path).removesuffix(".flow-meta.xml")
        key = self.m.component(
            "flow",
            name,
            path=path,
            line=root.line,
            process_type=root.child_text("processType"),
            status=root.child_text("status"),
        )
        start = root.find("start")
        if start is not None and (obj := start.find("object")) is not None and obj.text.strip():
            trigger = start.child_text("recordTriggerType") or start.child_text("triggerType")
            target, classification, reason = self.object_target(obj.text.strip())
            self.m.edge(
                key,
                target,
                "flow_triggers_on",
                classification,
                obj.text.strip(),
                f"{trigger or 'record-triggered'}; {reason}",
                path=path,
                line=obj.line,
                text=self.evidence(path, obj.line),
            )
        for operation in ("recordLookups", "recordCreates", "recordUpdates", "recordDeletes"):
            for element in root.findall(operation):
                obj = element.find("object")
                if obj is None or not obj.text.strip():
                    continue
                target, classification, reason = self.object_target(obj.text.strip())
                self.m.edge(
                    key,
                    target,
                    "flow_uses_object",
                    classification,
                    obj.text.strip(),
                    f"{operation}; {reason}",
                    path=path,
                    line=obj.line,
                    text=self.evidence(path, obj.line),
                )
        for action in root.findall("actionCalls"):
            if (action.child_text("actionType") or "").lower() != "apex":
                continue
            action_name = action.find("actionName")
            if action_name is None or not action_name.text.strip():
                continue
            target, classification, reason = self.class_target(action_name.text.strip(), None)
            self.m.edge(
                key,
                target,
                "flow_calls_apex",
                classification,
                action_name.text.strip(),
                f"invocable action; {reason}",
                path=path,
                line=action_name.line,
                text=self.evidence(path, action_name.line),
            )

    def permission_set(self, path: str) -> None:
        root = self.xml(path, "permissions")
        if root is None:
            return
        name = basename(path).removesuffix(".permissionset-meta.xml")
        key = self.m.component("permission_set", name, path=path, line=root.line)
        for element in root.findall("objectPermissions"):
            obj = element.find("object")
            if obj is None or not obj.text.strip():
                continue
            allowed = [
                op.removeprefix("allow")
                for op in ("allowRead", "allowCreate", "allowEdit", "allowDelete")
                if (element.child_text(op) or "").lower() == "true"
            ] + [
                op.removeprefix("modify").removeprefix("view")
                for op in ("viewAllRecords", "modifyAllRecords")
                if (element.child_text(op) or "").lower() == "true"
            ]
            target, classification, reason = self.object_target(obj.text.strip())
            self.m.edge(
                key,
                target,
                "grants_object_access",
                classification,
                obj.text.strip(),
                f"{', '.join(allowed) or 'no access'}; {reason}",
                path=path,
                line=obj.line,
                text=self.evidence(path, obj.line),
            )
        for element in root.findall("classAccesses"):
            cls = element.find("apexClass")
            if cls is None or (element.child_text("enabled") or "").lower() != "true":
                continue
            target, classification, reason = self.class_target(cls.text.strip(), None)
            self.m.edge(
                key,
                target,
                "grants_class_access",
                classification,
                cls.text.strip(),
                reason,
                path=path,
                line=cls.line,
                text=self.evidence(path, cls.line),
            )

    def custom_metadata_record(self, path: str) -> None:
        name = basename(path).removesuffix(".md-meta.xml")
        type_name, _, record = name.partition(".")
        if not record:
            return
        key = self.m.component("custom_metadata_record", name, path=path, line=1)
        target, classification, reason = self.object_target(f"{type_name}__mdt")
        self.m.edge(
            key,
            target,
            "record_of",
            classification,
            f"{type_name}__mdt",
            reason,
            path=path,
            line=1,
            text=None,
        )


def map_upload(data: GraphData, texts: dict[str, str], project: Project) -> PackReport:
    mapper = _SalesforceMapper(data, texts)
    mapper.run()
    version = project.source_api_version
    number = api_version(version)
    if number is None:
        versions = [
            v
            for p, t in texts.items()
            if p.endswith("-meta.xml")
            for v in [api_version(m.group(1)) for m in re.finditer(r"<apiVersion>([^<]+)<", t)]
            if v is not None
        ]
        if versions:
            number = max(versions)
            version = f"{number:.1f}"
    if number is None:
        status = VersionStatus.UNKNOWN
    elif number <= RETIRED_MAX:
        status = VersionStatus.UNSUPPORTED
    else:
        status = VersionStatus.SUPPORTED
    counts = mapper.m.relations

    def state(present: bool, failed: int = 0) -> str:
        if not present:
            return CapabilityState.UNAVAILABLE
        return CapabilityState.PARTIAL if failed else CapabilityState.AVAILABLE

    has = {
        "objects": any(p.endswith((".object-meta.xml", ".field-meta.xml")) for p in texts),
        "flows": any(p.endswith(".flow-meta.xml") for p in texts),
        "permissions": any(p.endswith(".permissionset-meta.xml") for p in texts),
        "lwc": any("/lwc/" in f"/{p}" for p in texts),
    }
    capabilities = [
        Capability(
            "project",
            "Project and packages",
            state(bool(project.modules), project.malformed),
            f"{len(project.modules)} package directories from sfdx-project.json",
        ),
        Capability(
            "apex",
            "Apex classes and triggers",
            state(
                any(p.endswith((".cls", ".trigger")) for p in texts), mapper.failures.get("apex", 0)
            ),
            "declarations, sharing mode, entry points and static SOQL objects (masked comments "
            f"and strings); {mapper.dynamic_queries} dynamic queries cannot be resolved; no call "
            "graph between classes",
        ),
        Capability(
            "lwc",
            "Lightning Web Components to Apex and schema",
            state(has["lwc"]),
            "@salesforce/apex and @salesforce/schema imports; Aura and Visualforce not mapped",
        ),
        Capability(
            "objects",
            "Objects, fields and lookups",
            state(
                has["objects"], mapper.failures.get("objects", 0) + mapper.failures.get("fields", 0)
            ),
            "object and field metadata in the upload; standard objects without metadata are "
            "declared, custom ones unresolved",
        ),
        Capability(
            "flows",
            "Flows",
            state(has["flows"], mapper.failures.get("flows", 0)),
            "trigger object, record operations and Apex actions",
        ),
        Capability(
            "permissions",
            "Permission sets and sharing",
            state(has["permissions"] or has["objects"], mapper.failures.get("permissions", 0)),
            "permission-set object/class access and object sharing models supplied as "
            "metadata; profiles and sharing rules in an org are not visible",
        ),
        Capability(
            "org_validation",
            "Org deployment and Apex tests",
            CapabilityState.UNAVAILABLE,
            "needs an authorized Salesforce org; source-level parsing does not prove deployability",
        ),
    ]
    notes = sorted(set(project.notes + mapper.notes))[:50]
    if status == VersionStatus.UNKNOWN:
        notes.insert(0, "No API version declared (sourceApiVersion or apiVersion in metadata).")
    elif status == VersionStatus.UNSUPPORTED:
        notes.insert(
            0,
            f"API version {version} is in the retired range (21.0-30.0); calls at this "
            "version fail and components keep legacy behaviour.",
        )
    return PackReport(
        id=ID,
        name=NAME,
        adapter=ADAPTER,
        status="experimental",
        version=version,
        version_status=status,
        version_evidence=project.version_evidence,
        supported_versions=SUPPORTED_TEXT,
        capabilities=capabilities,
        relations=dict(sorted(counts.items())),
        components=dict(sorted(mapper.m.components.items())),
        rules=list(RULES),
        notes=notes,
    )
