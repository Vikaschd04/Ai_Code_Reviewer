"""Intended architecture as code (P10 slice 2; ADR 0019).

A team declares, per project:

- **layers**, top to bottom, each a list of patterns over the parts of the architecture model
  (Java packages and folders, as in Structure health);
- **layering**: ``lower`` (a layer may use any layer below it), ``next`` (only the layer directly
  below it) or ``none`` (layers only name groups for the other rules);
- **forbid** rules: ``from`` must not use ``to`` (a layer name or a pattern);
- **allow** exceptions to layering, each with a reason and an optional expiry date.

Every review evaluates the rules on the file-level dependencies of the upload's dependency map.
Precedence: a forbidden dependency is always reported; otherwise two different layers follow the
layering unless an unexpired exception allows the use. Parts in no layer are checked only by
forbid rules, and dependencies inside one part are never checked. Test code is left out (as in
Structure health).

Patterns: name parts are separated by dots or slashes; ``*`` matches within one part and ``**``
any number of parts, including none (``com.acme.web.**`` is the package and everything below
it). A part belongs to the first layer that matches it.

Each rule has its own content hash (``rule_hashes``), so a recheck tells "the code no longer
breaks this rule" from "the rule changed". Severity and reasons are not part of the hash: they do
not change what is detected.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import cache

import yaml

from crp_analysis.architecture.metrics import CodeFile, Dependency, component_of

SCHEMA = "crp-architecture-rules-v1"
LAYERS_RULE = "arch.layers"
FORBID_PREFIX = "arch.forbid."
LAYERING = ("lower", "next", "none")
SEVERITIES = ("critical", "high", "medium", "low")
DEFAULT_SEVERITY = "medium"
MAX_YAML_BYTES = 64 * 1024
MAX_LAYERS = 30
MAX_PATTERNS = 20
MAX_RULES = 100
MAX_TEXT = 300

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,39}$")
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_PATTERN = re.compile(r"^[A-Za-z0-9_$@.*/-]{1,200}$")
_SEP = re.compile(r"[./]")


class RulesError(ValueError):
    """The rules are not valid; ``problems`` says where and why (plain language)."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Layer:
    name: str
    match: tuple[str, ...]
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Forbid:
    key: str
    source: str  # "from": a layer name or a pattern
    target: str  # "to"
    reason: str | None = None
    severity: str = DEFAULT_SEVERITY


@dataclass(frozen=True, slots=True)
class Allow:
    source: str
    target: str
    reason: str | None = None
    until: date | None = None


@dataclass(frozen=True, slots=True)
class RuleSet:
    layers: tuple[Layer, ...] = ()
    layering: str = "lower"
    severity: str = DEFAULT_SEVERITY  # for layering breaches
    forbid: tuple[Forbid, ...] = ()
    allow: tuple[Allow, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.forbid and not self.layering_active

    @property
    def layering_active(self) -> bool:
        return self.layering != "none" and len(self.layers) > 1

    def layer(self, name: str) -> Layer | None:
        return next((item for item in self.layers if item.name == name), None)

    def to_document(self) -> dict[str, object]:
        """The canonical JSON form (stored, hashed, returned by the API)."""
        return {
            "schema": SCHEMA,
            "layers": [
                {
                    "name": item.name,
                    "match": list(item.match),
                    **({"description": item.description} if item.description else {}),
                }
                for item in self.layers
            ],
            "layering": self.layering,
            "severity": self.severity,
            "forbid": [
                {
                    "key": rule.key,
                    "from": rule.source,
                    "to": rule.target,
                    "severity": rule.severity,
                    **({"reason": rule.reason} if rule.reason else {}),
                }
                for rule in self.forbid
            ],
            "allow": [_allow_document(rule, iso=True) for rule in self.allow],
        }

    def sha256(self) -> str:
        return _digest(self.to_document())

    def rule_ids(self) -> list[str]:
        ids = [LAYERS_RULE] if self.layering_active else []
        return ids + [FORBID_PREFIX + rule.key for rule in self.forbid]

    def rule_hashes(self) -> dict[str, str]:
        """Per rule: the content that decides what it detects."""
        layers = [{"name": item.name, "match": list(item.match)} for item in self.layers]
        hashes: dict[str, str] = {}
        if self.layering_active:
            hashes[LAYERS_RULE] = _digest(
                {
                    "schema": SCHEMA,
                    "layers": layers,
                    "layering": self.layering,
                    "allow": [
                        [a.source, a.target, a.until.isoformat() if a.until else None]
                        for a in self.allow
                    ],
                }
            )
        names = {item.name for item in self.layers}
        for rule in self.forbid:
            uses_layers = rule.source in names or rule.target in names
            hashes[FORBID_PREFIX + rule.key] = _digest(
                {
                    "schema": SCHEMA,
                    "from": rule.source,
                    "to": rule.target,
                    "layers": layers if uses_layers else None,
                }
            )
        return hashes


def _allow_document(rule: Allow, *, iso: bool) -> dict[str, object]:
    """``iso``: the expiry as text (JSON) rather than a date (YAML export)."""
    document: dict[str, object] = {"from": rule.source, "to": rule.target}
    if rule.reason:
        document["reason"] = rule.reason
    if rule.until:
        document["until"] = rule.until.isoformat() if iso else rule.until
    return document


def _digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# -- parsing and validation -----------------------------------------------------------------------


def _text(value: object, where: str, problems: list[str], *, required: bool = False) -> str | None:
    if value is None:
        if required:
            problems.append(f"{where}: required")
        return None
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}: must be text")
        return None
    if len(value) > MAX_TEXT:
        problems.append(f"{where}: at most {MAX_TEXT} characters")
        return None
    return value.strip()


def _keys(item: Mapping[str, object], allowed: set[str], where: str, problems: list[str]) -> None:
    unknown = sorted(str(k) for k in item if k not in allowed)
    if unknown:
        problems.append(f"{where}: unknown field {', '.join(unknown)}")


def _pattern_problem(pattern: str) -> str | None:
    if pattern == ".":
        return None
    if not _PATTERN.match(pattern):
        return "use letters, digits, . / * _ - $ @ only"
    if any(part == "" for part in _SEP.split(pattern)):
        return "empty name part (two separators in a row, or a leading or trailing one)"
    return None


def _selector(value: object, where: str, names: dict[str, str], problems: list[str]) -> str | None:
    text = _text(value, where, problems, required=True)
    if text is None:
        return None
    if text.lower() in names:
        return names[text.lower()]
    problem = _pattern_problem(text)
    if problem:
        problems.append(f"{where}: {text!r} is not a layer name or a valid pattern ({problem})")
        return None
    return text


def _severity(value: object, where: str, problems: list[str]) -> str:
    if value is None:
        return DEFAULT_SEVERITY
    if not isinstance(value, str) or value.lower() not in SEVERITIES:
        problems.append(f"{where}: one of {', '.join(SEVERITIES)}")
        return DEFAULT_SEVERITY
    return value.lower()


def _date(value: object, where: str, problems: list[str]) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    problems.append(f"{where}: a date like 2026-12-31")
    return None


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48].strip("-") or "rule"


def _list(value: object, where: str, problems: list[str], limit: int) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(f"{where}: must be a list")
        return []
    if len(value) > limit:
        problems.append(f"{where}: at most {limit} entries")
        return []
    return value


def from_document(data: object) -> RuleSet:
    """Validate a rules document (parsed JSON or YAML). Raises ``RulesError``."""
    problems: list[str] = []
    if data is None:
        return RuleSet()
    if not isinstance(data, dict):
        raise RulesError(["the rules must be a mapping with layers, forbid and allow"])
    _keys(data, {"schema", "layers", "layering", "severity", "forbid", "allow"}, "rules", problems)
    schema = data.get("schema", SCHEMA)
    if schema != SCHEMA:
        problems.append(f"schema: must be {SCHEMA}")
    layers: list[Layer] = []
    names: dict[str, str] = {}
    for index, raw in enumerate(_list(data.get("layers"), "layers", problems, MAX_LAYERS)):
        where = f"layers[{index + 1}]"
        if not isinstance(raw, dict):
            problems.append(f"{where}: must be a mapping with name and match")
            continue
        _keys(raw, {"name", "match", "description"}, where, problems)
        name = _text(raw.get("name"), f"{where}.name", problems, required=True)
        if name is not None and not _NAME.match(name):
            problems.append(
                f"{where}.name: start with a letter; letters, digits, spaces, _ and - (40 at most)"
            )
            name = None
        if name is not None and name.lower() in names:
            problems.append(f"{where}.name: {name!r} is used twice")
            name = None
        patterns: list[str] = []
        before = len(problems)
        match = raw.get("match")
        if isinstance(match, str):
            match = [match]
        for p_index, pattern in enumerate(_list(match, f"{where}.match", problems, MAX_PATTERNS)):
            text = _text(pattern, f"{where}.match[{p_index + 1}]", problems)
            if text is None:
                continue
            problem = _pattern_problem(text)
            if problem:
                problems.append(f"{where}.match[{p_index + 1}]: {problem}")
                continue
            patterns.append(text)
        if match is None:
            problems.append(f"{where}.match: required")
        elif not patterns and len(problems) == before:
            problems.append(f"{where}.match: at least one pattern")
        description = _text(raw.get("description"), f"{where}.description", problems)
        if name is not None:
            names[name.lower()] = name
            layers.append(Layer(name, tuple(patterns), description))
    layering = data.get("layering", "lower")
    if layering not in LAYERING:
        problems.append(f"layering: one of {', '.join(LAYERING)}")
        layering = "lower"
    severity = _severity(data.get("severity"), "severity", problems)
    forbid: list[Forbid] = []
    keys: set[str] = set()
    for index, raw in enumerate(_list(data.get("forbid"), "forbid", problems, MAX_RULES)):
        where = f"forbid[{index + 1}]"
        if not isinstance(raw, dict):
            problems.append(f"{where}: must be a mapping with from and to")
            continue
        _keys(raw, {"key", "from", "to", "reason", "severity"}, where, problems)
        source = _selector(raw.get("from"), f"{where}.from", names, problems)
        target = _selector(raw.get("to"), f"{where}.to", names, problems)
        key = _text(raw.get("key"), f"{where}.key", problems)
        if key is not None and not _KEY.match(key):
            problems.append(f"{where}.key: lowercase letters, digits and - (48 at most)")
            key = None
        if source is None or target is None:
            continue
        if source == target:
            problems.append(f"{where}: from and to are the same")
            continue
        key = key or _slug(f"{source}-to-{target}")
        if key in keys:
            problems.append(f"{where}.key: {key!r} is used twice; give each rule its own key")
            continue
        keys.add(key)
        forbid.append(
            Forbid(
                key,
                source,
                target,
                _text(raw.get("reason"), f"{where}.reason", problems),
                _severity(raw.get("severity"), f"{where}.severity", problems),
            )
        )
    allow: list[Allow] = []
    for index, raw in enumerate(_list(data.get("allow"), "allow", problems, MAX_RULES)):
        where = f"allow[{index + 1}]"
        if not isinstance(raw, dict):
            problems.append(f"{where}: must be a mapping with from, to and reason")
            continue
        _keys(raw, {"from", "to", "reason", "until"}, where, problems)
        source = _selector(raw.get("from"), f"{where}.from", names, problems)
        target = _selector(raw.get("to"), f"{where}.to", names, problems)
        reason = _text(raw.get("reason"), f"{where}.reason", problems, required=True)
        until = _date(raw.get("until"), f"{where}.until", problems)
        if source is not None and target is not None and reason is not None:
            allow.append(Allow(source, target, reason, until))
    if problems:
        raise RulesError(problems[:50])
    return RuleSet(tuple(layers), str(layering), severity, tuple(forbid), tuple(allow))


def parse_yaml(text: str) -> RuleSet:
    """Parse YAML rules as data: one document, standard types only, no anchors or aliases (no
    expansion attacks), at most 64 KB. Raises ``RulesError``."""
    if len(text.encode("utf-8")) > MAX_YAML_BYTES:
        raise RulesError([f"the file is larger than {MAX_YAML_BYTES // 1024} KB"])
    try:
        documents = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.DocumentStartEvent):
                documents += 1
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise RulesError(["YAML anchors and aliases (& and *) are not supported"])
        if documents > 1:
            raise RulesError(["the file must hold one YAML document"])
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}: " if mark is not None else ""
        problem = getattr(exc, "problem", None) or "not valid YAML"
        raise RulesError([f"{where}{problem}"]) from None
    return from_document(data)


_YAML_HEADER = """\
# refactorX architecture rules ({schema})
# layers: top to bottom; each part (Java package or folder) belongs to the first layer that
#   matches it. Patterns: * matches within one name part, ** any number of parts.
# layering: lower (use any layer below), next (only the layer directly below) or none.
# forbid: from must not use to (a layer name or a pattern); always reported.
# allow: exceptions to layering, with a reason and an optional expiry date (until).
"""


def to_yaml(rules: RuleSet) -> str:
    document = rules.to_document()
    document["allow"] = [_allow_document(rule, iso=False) for rule in rules.allow]
    body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
    return _YAML_HEADER.format(schema=SCHEMA) + body


# -- patterns --------------------------------------------------------------------------------------


def _segments(key: str) -> tuple[str, ...]:
    return () if key in {"", "."} else tuple(_SEP.split(key))


@cache
def _compiled(pattern: str) -> tuple[re.Pattern[str] | None, ...]:
    return tuple(
        None
        if part == "**"
        else re.compile("".join("[^./]*" if ch == "*" else re.escape(ch) for ch in part))
        for part in _segments(pattern)
    )


def matches(pattern: str, key: str) -> bool:
    """Does a part key (``com.acme.web``, ``src/web`` or ``.``) match a pattern?"""
    segments = _segments(key)
    reachable = {0}
    for part in _compiled(pattern):
        if part is None:
            reachable = set(range(min(reachable), len(segments) + 1))
        else:
            reachable = {
                i + 1 for i in reachable if i < len(segments) and part.fullmatch(segments[i])
            }
        if not reachable:
            return False
    return len(segments) in reachable


# -- evaluation ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Violation:
    rule_id: str
    rule_sha256: str
    source_path: str
    target: str  # a file path, or a package name for an on-demand import
    line: int | None
    source_component: str
    target_component: str
    source_layer: str | None
    target_layer: str | None
    severity: str
    title: str
    message: str
    reason: str | None


@dataclass(slots=True)
class Evaluation:
    violations: list[Violation] = field(default_factory=list)
    layer_of: dict[str, str | None] = field(default_factory=dict)  # part -> layer
    overlaps: dict[str, list[str]] = field(default_factory=dict)  # part -> matching layers
    unmatched: list[str] = field(default_factory=list)  # references that match no part
    expired: list[Allow] = field(default_factory=list)
    allowed: int = 0  # dependencies an exception allowed
    dependencies_checked: int = 0
    rule_hashes: dict[str, str] = field(default_factory=dict)

    @property
    def unassigned(self) -> list[str]:
        return sorted(part for part, layer in self.layer_of.items() if layer is None)


def _below(rules: RuleSet, layer: str) -> str:
    names = [item.name for item in rules.layers]
    lower = names[names.index(layer) + 1 :]
    if not lower:
        return f"{layer} is the bottom layer and may not use other layers"
    if rules.layering == "next":
        return f"{layer} may only use the layer directly below it ({lower[0]})"
    return f"{layer} may only use the layers below it ({', '.join(lower)})"


def evaluate(
    rules: RuleSet,
    files: Iterable[CodeFile],
    dependencies: Iterable[Dependency],
    *,
    today: date,
) -> Evaluation:
    result = Evaluation(rule_hashes=rules.rule_hashes())
    component: dict[str, str] = {}
    for file in files:
        if not file.test:
            component[file.path] = component_of(file)[0]
    parts = sorted(set(component.values()))
    order = {item.name: index for index, item in enumerate(rules.layers)}
    members: dict[str, set[str]] = defaultdict(set)
    for part in parts:
        hits = [item.name for item in rules.layers if any(matches(p, part) for p in item.match)]
        result.layer_of[part] = hits[0] if hits else None
        if hits:
            members[hits[0]].add(part)
        if len(hits) > 1:
            result.overlaps[part] = hits

    @cache
    def selected(selector: str) -> frozenset[str]:
        if selector in order:
            return frozenset(members[selector])
        return frozenset(part for part in parts if matches(selector, part))

    for rule in rules.forbid:
        for end, selector in (("from", rule.source), ("to", rule.target)):
            if not selected(selector):
                result.unmatched.append(f'forbid "{rule.key}": {end} {selector!r} matches no part')
    active: list[Allow] = []
    for allow in rules.allow:
        if allow.until is not None and allow.until < today:
            result.expired.append(allow)
        else:
            active.append(allow)

    # One finding per (source file, target), anchored at its first evidence line.
    part_set = set(parts)
    pairs: dict[tuple[str, str], tuple[str, str, int | None]] = {}
    for dep in dependencies:
        source = component.get(dep.source_path)
        if source is None:
            continue
        if dep.target_path is not None:
            target, label = component.get(dep.target_path), dep.target_path
        elif dep.target_package is not None and dep.target_package in part_set:
            target, label = dep.target_package, dep.target_package
        else:
            continue
        if target is None or target == source:
            continue
        known = pairs.get((dep.source_path, label))
        if known is None or (dep.line is not None and (known[2] is None or dep.line < known[2])):
            pairs[(dep.source_path, label)] = (source, target, dep.line)
    result.dependencies_checked = len(pairs)

    for (path, label), (source, target, line) in sorted(pairs.items()):
        source_layer, target_layer = result.layer_of.get(source), result.layer_of.get(target)
        forbidden = next(
            (
                r
                for r in rules.forbid
                if source in selected(r.source) and target in selected(r.target)
            ),
            None,
        )
        if forbidden is not None:
            rule_id = FORBID_PREFIX + forbidden.key
            result.violations.append(
                Violation(
                    rule_id=rule_id,
                    rule_sha256=result.rule_hashes[rule_id],
                    source_path=path,
                    target=label,
                    line=line,
                    source_component=source,
                    target_component=target,
                    source_layer=source_layer,
                    target_layer=target_layer,
                    severity=forbidden.severity,
                    title=f"Forbidden dependency: {forbidden.source} → {forbidden.target}"[:300],
                    message=(
                        f"This file uses {label} ({source} → {target}). The architecture rule "
                        f'"{forbidden.key}" says {forbidden.source} must not use '
                        f"{forbidden.target}."
                        + (f" Reason: {forbidden.reason}" if forbidden.reason else "")
                    ),
                    reason=forbidden.reason,
                )
            )
            continue
        if (
            not rules.layering_active
            or source_layer is None
            or target_layer is None
            or source_layer == target_layer
        ):
            continue
        step = order[target_layer] - order[source_layer]
        if step == 1 or (step > 1 and rules.layering == "lower"):
            continue
        if any(source in selected(a.source) and target in selected(a.target) for a in active):
            result.allowed += 1
            continue
        result.violations.append(
            Violation(
                rule_id=LAYERS_RULE,
                rule_sha256=result.rule_hashes[LAYERS_RULE],
                source_path=path,
                target=label,
                line=line,
                source_component=source,
                target_component=target,
                source_layer=source_layer,
                target_layer=target_layer,
                severity=rules.severity,
                title=f"Layer {source_layer} must not use {target_layer}"[:300],
                message=(
                    f"This file in layer {source_layer} uses {label} in layer {target_layer} "
                    f"({source} → {target}). {_below(rules, source_layer)}."
                ),
                reason=None,
            )
        )
    return result
