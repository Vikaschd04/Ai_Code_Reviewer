"""Labelled evaluation of the architecture smells (P10 slice 3; ADR 0020).

Each case folder of ``fixtures/architecture-eval`` is analyzed like an upload: the scope policy,
the real syntax extraction and resolution (no database), the architecture model and the smell
detectors. ``labels.json`` lists every expected finding as (rule, path); precision and recall are
computed per smell. The set is synthetic and hand-labelled: it measures that the detectors meet
their specification across Java, TypeScript, SAP Commerce and Salesforce layouts, not how useful
the warnings are on real projects.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from crp_analysis import policy as scope_policy
from crp_analysis import structure
from crp_analysis.architecture.model import model_from_graph
from crp_analysis.architecture.smells import RULES, SmellReport, detect
from crp_analysis.graph.extract import FileFacts, extract_facts
from crp_analysis.graph.manifests import (
    Module,
    TsConfig,
    parse_package_json,
    parse_pom,
    parse_tsconfig,
)
from crp_analysis.graph.resolve import build_graph

SCHEMA = "crp-arch-eval-v1"


def analyze(root: Path) -> SmellReport:
    """Smells of one folder, analyzed like an upload (in memory)."""
    sources: dict[str, str | None] = {}
    facts: dict[str, FileFacts] = {}
    known: dict[str, str] = {}
    lines: dict[str, int | None] = {}
    modules: list[Module] = []
    tsconfigs: list[TsConfig] = []
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        path = file.relative_to(root).as_posix()
        classification = scope_policy.classify(path)
        if classification.category == "excluded":
            continue
        known[path] = "ANALYZABLE"
        data = file.read_bytes()
        lines[path] = len(data.decode("utf-8", errors="replace").splitlines())
        language = classification.language
        if structure.grammar_for(path, language) is not None:
            sources[path] = language
            facts[path] = extract_facts(data, path, language)
        name = posixpath.basename(path)
        if name == "pom.xml":
            modules.append(parse_pom(path, data))
        elif name == "package.json":
            modules.append(parse_package_json(path, data))
        elif name == "tsconfig.json":
            config, _ = parse_tsconfig(path, data)
            if config is not None:
                tsconfigs.append(config)
    graph = build_graph(
        sources=sources, facts=facts, known_files=known, modules=modules, tsconfigs=tsconfigs
    )
    model = model_from_graph(graph, lines)
    return detect(model.files, model.types, model.dependencies)


@dataclass(slots=True)
class RuleScore:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float | None:
        found = self.true_positives + self.false_positives
        return round(self.true_positives / found, 3) if found else None

    @property
    def recall(self) -> float | None:
        expected = self.true_positives + self.false_negatives
        return round(self.true_positives / expected, 3) if expected else None


@dataclass(slots=True)
class EvaluationResult:
    cases: int = 0
    scores: dict[str, RuleScore] = field(default_factory=lambda: {r: RuleScore() for r in RULES})
    mismatches: list[str] = field(default_factory=list)  # "case: missed|unexpected rule path"
    positives: dict[str, int] = field(default_factory=lambda: dict.fromkeys(RULES, 0))
    negatives: dict[str, int] = field(default_factory=lambda: dict.fromkeys(RULES, 0))

    def to_json(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "cases": self.cases,
            "rules": {
                rule: {
                    "true_positives": score.true_positives,
                    "false_positives": score.false_positives,
                    "false_negatives": score.false_negatives,
                    "precision": score.precision,
                    "recall": score.recall,
                    "positive_cases": self.positives[rule],
                    "negative_cases": self.negatives[rule],
                }
                for rule, score in self.scores.items()
            },
            "mismatches": self.mismatches,
        }


def evaluate(root: Path) -> EvaluationResult:
    labels = json.loads((root / "labels.json").read_text(encoding="utf-8"))
    if labels.get("schema") != SCHEMA:
        raise ValueError(f"labels.json must use schema {SCHEMA}")
    result = EvaluationResult()
    for name, case in sorted(labels["cases"].items()):
        result.cases += 1
        expected = {(item["rule"], item["path"]) for item in case["expected"]}
        found = {(s.rule_id, s.path) for s in analyze(root / "cases" / name).smells}
        for rule in RULES:
            wanted = {e for e in expected if e[0] == rule}
            got = {f for f in found if f[0] == rule}
            if wanted:
                result.positives[rule] += 1
            else:
                result.negatives[rule] += 1
            score = result.scores[rule]
            score.true_positives += len(wanted & got)
            score.false_positives += len(got - wanted)
            score.false_negatives += len(wanted - got)
            result.mismatches.extend(f"{name}: missed {r} {p}" for r, p in sorted(wanted - got))
            result.mismatches.extend(f"{name}: unexpected {r} {p}" for r, p in sorted(got - wanted))
    return result
