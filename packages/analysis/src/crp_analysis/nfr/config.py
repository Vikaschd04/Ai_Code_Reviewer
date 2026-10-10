"""Configuration and infrastructure evidence for the NFR questionnaire (P12 slice 2, ADR 0023).

Reads deployment manifests and application configuration as data (nothing is rendered, executed
or downloaded) and returns two things:

- **gaps**: conservative, deterministic rules that become tracked issues of the ``nfr`` engine
  (a single instance, no readiness probe, stop-the-world rollouts, risky Spring Boot settings);
- **signals**: supporting mechanisms with file and line (more than one instance, autoscaling,
  disruption budgets, probes, graceful shutdown, timeouts, pool sizes, backups, multi-zone).

Kubernetes: ``Deployment`` and ``StatefulSet`` documents in YAML files. A workload's effective
minimum is the largest value any document in the upload declares for it (base manifests, overlay
patches, ``kustomization`` replica counts, the minimum of an autoscaler that targets it), so an
overlay that raises replicas clears the base. A templated value (Helm, Jinja) is not judged.

Spring Boot: ``application*`` and ``bootstrap*`` properties and YAML, keys compared in relaxed
form (case, ``-`` and ``_`` ignored). Files and documents for development-like profiles (dev,
local, test, ...) are ignored. Placeholders (``${...}``) are not judged.

Terraform: only two supporting signals are read line by line (backups, multi-zone); its security
settings are not checked (Trivy's Terraform scanner downloads remote modules, ADR 0023).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import yaml

CONFIG_VERSION = "crp-nfr-config-v1"
MAX_NODES = 20_000  # flattening bound per file (anchors and aliases cannot blow it up)

SINGLE_REPLICA = "crp.nfr.k8s.single-replica"
NO_READINESS = "crp.nfr.k8s.no-readiness-probe"
RECREATE = "crp.nfr.k8s.recreate-strategy"
ACTUATOR_EXPOSED = "crp.nfr.spring.actuator-exposed"
HEALTH_DETAILS = "crp.nfr.spring.health-details-public"
SCHEMA_AUTO = "crp.nfr.spring.schema-auto-update"
RULES = (SINGLE_REPLICA, NO_READINESS, RECREATE, ACTUATOR_EXPOSED, HEALTH_DETAILS, SCHEMA_AUTO)

WORKLOAD_KINDS = frozenset({"Deployment", "StatefulSet"})
DEV_PROFILES = frozenset(
    {"dev", "development", "local", "test", "tests", "testing", "it", "e2e", "ci", "h2", "demo"}
)
# Enabled by default and only gated by web exposure (shutdown is disabled unless switched on).
SENSITIVE_ENDPOINTS = ("heapdump", "env", "configprops", "threaddump")
_SPRING_FILE = re.compile(r"^(application|bootstrap)(?:-([\w.-]+))?\.(properties|ya?ml)$")
_TOP_LEVEL_KIND = re.compile(r"(?m)^kind:[ \t]*[A-Za-z]")
_TEMPLATE = re.compile(r"\{\{|\{%")
_PLACEHOLDER = re.compile(r"\$\{")


class ConfigError(ValueError):
    """A file that should have been read could not be (reason is safe to show)."""


@dataclass(frozen=True, slots=True)
class Gap:
    rule_id: str
    path: str
    line: int | None
    message: str
    identity: str
    severity: str | None = None  # overrides the catalog severity when set
    title: str | None = None


@dataclass(frozen=True, slots=True)
class Hit:
    signal: str
    path: str
    line: int | None
    detail: str | None


@dataclass(slots=True)
class Findings:
    gaps: list[Gap] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # path -> reason
    skipped: dict[str, str] = field(default_factory=dict)  # path -> reason (not attempted)
    workloads: int = 0
    spring_files: int = 0


# -- YAML as nodes (line numbers kept) ------------------------------------------------------------


def _documents(text: str) -> list[yaml.Node]:
    try:
        return [n for n in yaml.compose_all(text, Loader=yaml.SafeLoader) if n is not None]
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}: " if mark is not None else ""
        problem = getattr(exc, "problem", None) or "error"
        raise ConfigError(f"not valid YAML ({where}{problem})") from None
    except RecursionError:
        raise ConfigError("nested too deeply to read") from None


def _get(node: yaml.Node | None, key: str) -> yaml.Node | None:
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if isinstance(k, yaml.ScalarNode) and k.value == key:
                return v if isinstance(v, yaml.Node) else None
    return None


def _path(node: yaml.Node | None, *keys: str) -> yaml.Node | None:
    for key in keys:
        node = _get(node, key)
    return node


def _text(node: yaml.Node | None) -> str | None:
    return node.value if isinstance(node, yaml.ScalarNode) else None


def _int(node: yaml.Node | None) -> int | None:
    value = _text(node)
    return int(value) if value is not None and value.isdigit() else None


def _line(node: yaml.Node | None) -> int | None:
    return node.start_mark.line + 1 if node is not None else None


def _items(node: yaml.Node | None) -> list[yaml.Node]:
    return list(node.value) if isinstance(node, yaml.SequenceNode) else []


def _key_line(node: yaml.Node | None, key: str) -> int | None:
    if isinstance(node, yaml.MappingNode):
        for k, _ in node.value:
            if isinstance(k, yaml.ScalarNode) and k.value == key:
                return k.start_mark.line + 1
    return None


def parse_nodes(text: str) -> list[yaml.Node]:
    """The YAML documents as nodes with line numbers (for the configuration fix recipes)."""
    return _documents(text)


def child(node: yaml.Node | None, key: str) -> yaml.Node | None:
    return _get(node, key)


def key_line(node: yaml.Node | None, key: str) -> int | None:
    return _key_line(node, key)


def containers_of(node: yaml.Node) -> list[yaml.MappingNode]:
    """The containers of a workload document (``spec.template.spec.containers``)."""
    items = _items(_path(node, "spec", "template", "spec", "containers"))
    return [c for c in items if isinstance(c, yaml.MappingNode)]


def first_port(container: yaml.Node) -> str | None:
    """The container's first declared port (number, or name when it has no number)."""
    ports = _items(_get(container, "ports"))
    if not ports:
        return None
    number = _int(_get(ports[0], "containerPort"))
    name = _text(_get(ports[0], "name"))
    return str(number) if number is not None else name


# -- Kubernetes -----------------------------------------------------------------------------------


@dataclass(slots=True)
class _Doc:
    path: str
    kind: str
    name: str
    node: yaml.Node


_Declared = tuple[int, str, int | None, str]  # (count, path, line, what declares it)
_KustomizeCount = tuple[str, int, str, int | None]  # (workload name, count, path, line)


@dataclass(slots=True)
class _Replicas:
    declared: list[_Declared] = field(default_factory=list)  # spec.replicas and kustomization
    autoscaled: list[_Declared] = field(default_factory=list)  # autoscaler minimums
    unknown: bool = False  # a templated or non-numeric value somewhere: not judged

    def effective(self) -> list[_Declared]:
        """An autoscaler owns the replica count when one targets the workload."""
        return self.autoscaled or self.declared


def _k8s_documents(path: str, nodes: list[yaml.Node]) -> list[_Doc]:
    docs: list[_Doc] = []
    for node in nodes:
        kind, name = _text(_get(node, "kind")), _text(_path(node, "metadata", "name"))
        if kind and name and _text(_get(node, "apiVersion")):
            docs.append(_Doc(path, kind, name, node))
    return docs


def _containers(doc: _Doc) -> list[yaml.Node]:
    return _items(_path(doc.node, "spec", "template", "spec", "containers"))


def check_kubernetes(docs: list[_Doc], kustomize: list[_KustomizeCount]) -> Findings:
    """Rules over every Kubernetes document of the upload (overlays and autoscalers included)."""
    out = Findings()
    workloads: dict[tuple[str, str], list[_Doc]] = {}
    replicas: dict[tuple[str, str], _Replicas] = {}
    for doc in docs:
        if doc.kind in WORKLOAD_KINDS:
            workloads.setdefault((doc.kind, doc.name), []).append(doc)
    for (kind, name), group in workloads.items():
        state = replicas[(kind, name)] = _Replicas()
        for doc in group:
            node = _path(doc.node, "spec", "replicas")
            if node is None:
                continue
            value = _int(node)
            if value is None:
                state.unknown = True
            else:
                state.declared.append((value, doc.path, _line(node), f"replicas: {value}"))
        for k_name, count, k_path, k_line in kustomize:
            if k_name == name:
                state.declared.append((count, k_path, k_line, f"kustomization replicas: {count}"))
    for doc in docs:
        if doc.kind in {"HorizontalPodAutoscaler", "ScaledObject"}:
            target = _path(doc.node, "spec", "scaleTargetRef")
            t_name = _text(_get(target, "name"))
            t_kind = _text(_get(target, "kind")) or "Deployment"
            key = "minReplicas" if doc.kind == "HorizontalPodAutoscaler" else "minReplicaCount"
            node = _path(doc.node, "spec", key)
            minimum = _int(node) if node is not None else (1 if key == "minReplicas" else 0)
            out.hits.append(
                Hit("autoscaling", doc.path, _key_line(doc.node, "kind"), f"{doc.kind} {doc.name}")
            )
            target_state = replicas.get((t_kind, t_name or ""))
            if target_state is None:
                continue
            if minimum is None:
                target_state.unknown = True
            else:
                where = _line(node) if node is not None else _key_line(doc.node, "kind")
                target_state.autoscaled.append(
                    (minimum, doc.path, where, f"{doc.kind} {doc.name} minimum: {minimum}")
                )
        elif doc.kind == "PodDisruptionBudget":
            out.hits.append(
                Hit("disruption-budget", doc.path, _key_line(doc.node, "kind"), doc.name)
            )
    out.workloads = len(workloads)
    for (kind, name), group in sorted(workloads.items()):
        bases = [d for d in group if _containers(d)] or group
        base = min(bases, key=lambda d: d.path)
        state = replicas[(kind, name)]
        _replica_rule(out, kind, name, base, state)
        _probe_rules(out, kind, name, group)
        for doc in group:
            strategy = _path(doc.node, "spec", "strategy", "type")
            if kind == "Deployment" and _text(strategy) == "Recreate":
                out.gaps.append(
                    Gap(
                        RECREATE,
                        doc.path,
                        _line(strategy),
                        f"Deployment '{name}' uses the Recreate strategy: every release stops "
                        "all instances before starting new ones, so the service is down during "
                        "each deployment.",
                        f"recreate:{name}",
                    )
                )
    return out


def _replica_rule(out: Findings, kind: str, name: str, base: _Doc, state: _Replicas) -> None:
    if state.unknown:
        return
    values = state.effective()
    if values:
        best = max(values, key=lambda v: v[0])
        if best[0] >= 2:
            out.hits.append(
                Hit("multiple-instances", best[1], best[2], f"{kind} {name}: {best[3]}")
            )
            return
        shown = "; ".join(sorted({v[3] for v in values}))
        own = next((v for v in values if v[1] == base.path), best)
        path, line = own[1], own[2]
        message = f"{kind} '{name}' can run with fewer than two instances ({shown})."
    else:
        path, line = base.path, _key_line(base.node, "kind")
        message = (
            f"{kind} '{name}' runs a single instance: replicas is not set (Kubernetes runs 1) "
            "and no autoscaler in the upload targets it."
        )
    out.gaps.append(
        Gap(
            SINGLE_REPLICA,
            path,
            line,
            f"{message} A crash, a node failure or a rollout then makes it unavailable.",
            f"replicas:{kind}/{name}",
        )
    )


def _probe_rules(out: Findings, kind: str, name: str, group: list[_Doc]) -> None:
    probed: set[str] = set()
    for doc in group:
        for container in _containers(doc):
            c_name = _text(_get(container, "name")) or ""
            for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
                node = _get(container, probe)
                if node is not None:
                    if probe == "readinessProbe":
                        probed.add(c_name)
                    where = _key_line(container, probe)
                    out.hits.append(Hit("k8s-probes", doc.path, where, f"{c_name}: {probe}"))
    reported: set[str] = set()
    for doc in sorted(group, key=lambda d: d.path):
        for container in _containers(doc):
            c_name = _text(_get(container, "name")) or ""
            if c_name in probed or c_name in reported or not _items(_get(container, "ports")):
                continue  # probed, already reported, or serves no port (no traffic to gate)
            reported.add(c_name)
            out.gaps.append(
                Gap(
                    NO_READINESS,
                    doc.path,
                    _key_line(container, "name") or _line(container),
                    f"Container '{c_name}' of {kind} '{name}' serves a port but has no "
                    "readiness probe: traffic reaches instances that are still starting or "
                    "overloaded, which causes errors during rollouts and restarts.",
                    f"readiness:{kind}/{name}/{c_name}",
                )
            )


def _kustomize_replicas(path: str, nodes: list[yaml.Node]) -> list[_KustomizeCount]:
    counts: list[_KustomizeCount] = []
    for node in nodes:
        for item in _items(_get(node, "replicas")):
            name, count = _text(_get(item, "name")), _int(_get(item, "count"))
            if name and count is not None:
                counts.append((name, count, path, _line(_get(item, "count"))))
    return counts


# -- Spring Boot ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Prop:
    key: str  # relaxed: lower case, without - and _
    raw: str
    value: str
    line: int


def relaxed(key: str) -> str:
    return re.sub(r"[-_]", "", key.lower())


def parse_properties(text: str) -> list[list[_Prop]]:
    """Java properties as documents (``#---`` separates documents, as Spring Boot reads them)."""
    documents: list[list[_Prop]] = [[]]
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        start = index
        line = lines[index].lstrip()
        index += 1
        if line.rstrip() in {"#---", "!---"}:
            documents.append([])
            continue
        if not line or line[0] in "#!":
            continue
        while line.endswith("\\") and not line.endswith("\\\\") and index < len(lines):
            line = line[:-1] + lines[index].lstrip()
            index += 1
        match = re.match(r"((?:\\.|[^=:\s])+)\s*[=:\s]\s*(.*)$", line)
        if match is None:
            documents[-1].append(_Prop(relaxed(line.strip()), line.strip(), "", start + 1))
            continue
        raw = match.group(1).replace("\\", "")
        documents[-1].append(_Prop(relaxed(raw), raw, match.group(2).strip(), start + 1))
    return documents


def _flatten(node: yaml.Node, prefix: str, line: int, out: list[_Prop], budget: list[int]) -> None:
    budget[0] -= 1  # every visited node counts: aliases and recursive anchors cannot loop
    if budget[0] < 0:
        raise ConfigError(f"more than {MAX_NODES} settings (anchors expanded); not read")
    if isinstance(node, yaml.ScalarNode):
        out.append(_Prop(relaxed(prefix), prefix, node.value, line))
    elif isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode):
                name = f"{prefix}.{key.value}" if prefix else str(key.value)
                _flatten(value, name, key.start_mark.line + 1, out, budget)
    elif isinstance(node, yaml.SequenceNode):
        for position, item in enumerate(node.value):
            _flatten(item, f"{prefix}[{position}]", item.start_mark.line + 1, out, budget)


def parse_spring_yaml(text: str) -> list[list[_Prop]]:
    budget = [MAX_NODES]
    documents: list[list[_Prop]] = []
    for node in _documents(text):
        props: list[_Prop] = []
        try:
            _flatten(node, "", 1, props, budget)
        except RecursionError:
            raise ConfigError("nested too deeply to read") from None
        documents.append([p for p in props if p.raw])
    return documents


def _values(props: list[_Prop], key: str) -> list[_Prop]:
    """Entries for a key, including YAML list items (``key[0]``) and comma-separated values."""
    wanted = relaxed(key)
    item = re.compile(re.escape(wanted) + r"\[\d+\]")
    return [p for p in props if p.key == wanted or item.fullmatch(p.key)]


def _listed(props: list[_Prop]) -> list[str]:
    return [v.strip().lower() for p in props for v in p.value.split(",") if v.strip()]


def _profiles(props: list[_Prop]) -> set[str]:
    found = _values(props, "spring.config.activate.on-profile") + _values(props, "spring.profiles")
    return set(_listed(found))


_TIMEOUT_SUFFIXES = (
    "connecttimeout",
    "connectiontimeout",
    "readtimeout",
    "requesttimeout",
    "sockettimeout",
    "responsetimeout",
    "timeoutduration",
)
_POOL_KEYS = frozenset(
    relaxed(k)
    for k in (
        "spring.datasource.hikari.maximum-pool-size",
        "spring.datasource.tomcat.max-active",
        "spring.datasource.dbcp2.max-total",
        "spring.r2dbc.pool.max-size",
    )
)


def check_spring(path: str, documents: list[list[_Prop]], file_profile: str | None) -> Findings:
    out = Findings(spring_files=1)
    if file_profile is not None and file_profile.lower() in DEV_PROFILES:
        return out
    seen: set[str] = set()

    def hit(signal: str, prop: _Prop) -> None:
        if signal not in seen:  # one location per signal and file
            seen.add(signal)
            out.hits.append(Hit(signal, path, prop.line, prop.raw))

    for props in documents:
        profiles = _profiles(props)
        if profiles and profiles <= DEV_PROFILES:
            continue
        _actuator_rules(out, path, props)
        for prop in props:
            if _PLACEHOLDER.search(prop.value):
                continue
            value = prop.value.strip().lower()
            last = prop.key.rsplit(".", 1)[-1]
            if prop.key == relaxed("server.shutdown") and value == "graceful":
                hit("graceful-shutdown", prop)
            elif (
                prop.key
                in {
                    relaxed("management.endpoint.health.probes.enabled"),
                    relaxed("management.health.livenessstate.enabled"),
                    relaxed("management.health.readinessstate.enabled"),
                }
                and value == "true"
            ):
                hit("health-endpoints", prop)
            elif prop.key in _POOL_KEYS and value:
                hit("connection-pool", prop)
            elif last.endswith(_TIMEOUT_SUFFIXES) and value:
                hit("timeouts", prop)
            if prop.key.startswith("resilience4j.circuitbreaker."):
                hit("circuit-breakers", prop)
            elif prop.key.startswith("resilience4j.retry."):
                hit("retries", prop)
            elif prop.key in {
                relaxed("spring.jpa.hibernate.ddl-auto"),
                relaxed("spring.jpa.properties.hibernate.hbm2ddl.auto"),
            } and value in {"create", "create-drop", "update"}:
                drops = value != "update"
                out.gaps.append(
                    Gap(
                        SCHEMA_AUTO,
                        path,
                        prop.line,
                        f"{prop.raw}={prop.value}: Hibernate "
                        + (
                            "drops and recreates the database schema at startup, deleting the data."
                            if drops
                            else "changes the database schema at startup without a reviewed, "
                            "versioned migration; columns are never removed or fixed safely."
                        ),
                        f"ddl:{prop.key}",
                        severity="high" if drops else None,
                    )
                )
    return out


def _actuator_rules(out: Findings, path: str, props: list[_Prop]) -> None:
    include = _values(props, "management.endpoints.web.exposure.include")
    if include and not any(_PLACEHOLDER.search(p.value) for p in include):
        names = set(_listed(include))
        excluded = set(_listed(_values(props, "management.endpoints.web.exposure.exclude")))
        exposed = [
            e for e in SENSITIVE_ENDPOINTS if ("*" in names or e in names) and e not in excluded
        ]
        if exposed:
            out.gaps.append(
                Gap(
                    ACTUATOR_EXPOSED,
                    path,
                    include[0].line,
                    "Spring Boot Actuator exposes "
                    + ("all endpoints over HTTP, including " if "*" in names else "")
                    + ", ".join(exposed)
                    + ": a heap dump holds the application's memory (passwords, tokens, "
                    "personal data) and the others reveal its configuration and internals, "
                    "unless each of them requires authentication.",
                    "actuator:exposure",
                )
            )
    details = _values(props, "management.endpoint.health.show-details")
    if details and details[0].value.strip().lower() == "always":
        out.gaps.append(
            Gap(
                HEALTH_DETAILS,
                path,
                details[0].line,
                "management.endpoint.health.show-details=always shows every health component "
                "(databases, disk, external services and their errors) to anyone who can call "
                "the health endpoint.",
                "actuator:health-details",
            )
        )


# -- Terraform (supporting signals only) ---------------------------------------------------------

_TF_BACKUP = re.compile(
    r"^\s*(?:backup_retention_period\s*=\s*[1-9]\d*|point_in_time_recovery_enabled\s*=\s*true"
    r'|resource\s+"aws_backup_plan")'
)
_TF_ZONES = re.compile(
    r'^\s*(?:multi_az\s*=\s*true|zone_redundant\s*=\s*true|availability_type\s*=\s*"REGIONAL")'
)


def check_terraform(path: str, text: str) -> Findings:
    out = Findings()
    for signal, pattern in (("backups", _TF_BACKUP), ("multi-zone", _TF_ZONES)):
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.match(line):
                out.hits.append(Hit(signal, path, number, line.strip()[:120]))
                break
    return out


# -- one upload -----------------------------------------------------------------------------------


def is_candidate(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    if name == "pnpm-lock.yaml":
        return False
    return name.endswith((".yml", ".yaml", ".tf")) or bool(_SPRING_FILE.match(name))


def analyse(texts: dict[str, str]) -> Findings:
    """Gaps and signals for the candidate files of one upload (``path -> text``)."""
    out = Findings()
    docs: list[_Doc] = []
    kustomize: list[_KustomizeCount] = []
    for path, text in sorted(texts.items()):
        name = PurePosixPath(path).name
        spring = _SPRING_FILE.match(name)
        try:
            if name.endswith(".tf"):
                _merge(out, check_terraform(path, text))
            elif spring and name.endswith(".properties"):
                _merge(out, check_spring(path, parse_properties(text), spring.group(2)))
            elif spring:
                _merge(out, check_spring(path, parse_spring_yaml(text), spring.group(2)))
            elif name.lower() in {"kustomization.yaml", "kustomization.yml"}:
                kustomize.extend(_kustomize_replicas(path, _documents(text)))
            elif _TOP_LEVEL_KIND.search(text):
                docs.extend(_k8s_documents(path, _documents(text)))
        except ConfigError as exc:
            if _TEMPLATE.search(text):  # Helm or Jinja: only the rendered output is YAML
                out.skipped[path] = "template; its values are filled in at deploy time"
            else:
                out.failed[path] = str(exc)[:500]
    _merge(out, check_kubernetes(docs, kustomize))
    return out


def _merge(into: Findings, other: Findings) -> None:
    into.gaps.extend(other.gaps)
    into.hits.extend(other.hits)
    into.failed.update(other.failed)
    into.skipped.update(other.skipped)
    into.workloads += other.workloads
    into.spring_files += other.spring_files
