"""Static rule catalog: explanations, recommendations and canonical severity without AI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources

from crp_core.domain.states import FindingCategory, Severity


@dataclass(frozen=True, slots=True)
class RuleInfo:
    engine: str
    rule_id: str
    title: str
    category: FindingCategory
    severity: Severity
    severity_rationale: str
    explanation: str
    recommendation: str
    url: str | None
    in_catalog: bool = True
    family: str | None = None


@cache
def _load() -> dict[str, RuleInfo]:
    raw = json.loads(resources.files("crp_analysis.rules").joinpath("catalog.json").read_text())
    return {
        key: RuleInfo(
            engine=value["engine"],
            rule_id=value["rule_id"],
            title=value["title"],
            category=FindingCategory(value["category"]),
            severity=Severity(value["severity"]),
            severity_rationale=value["severity_rationale"],
            explanation=value["explanation"],
            recommendation=value["recommendation"],
            url=value["url"],
            family=value.get("family"),
        )
        for key, value in raw["rules"].items()
    }


def catalog_version() -> str:
    raw = json.loads(resources.files("crp_analysis.rules").joinpath("catalog.json").read_text())
    return str(raw["catalog_version"])


def all_rules() -> dict[str, RuleInfo]:
    return dict(_load())


_PMD_PRIORITY = {"1": Severity.HIGH, "2": Severity.MEDIUM, "3": Severity.MEDIUM, "4": Severity.LOW}
_ESLINT_SEVERITY = {"2": Severity.MEDIUM, "1": Severity.LOW}


def lookup(engine: str, rule_id: str, engine_severity: str | None, url: str | None) -> RuleInfo:
    """Catalog entry, or an explicit uncatalogued fallback (never silently dropped)."""
    if found := _load().get(f"{engine}:{rule_id}"):
        return found
    table = _PMD_PRIORITY if engine == "pmd" else _ESLINT_SEVERITY
    return RuleInfo(
        engine=engine,
        rule_id=rule_id,
        title=rule_id,
        category=FindingCategory.MAINTAINABILITY,
        severity=table.get(engine_severity or "", Severity.INFO),
        severity_rationale="Uncatalogued rule: severity mapped from the engine's own level.",
        explanation="This rule is not in the platform catalog; see the engine documentation.",
        recommendation="Review the engine documentation for this rule.",
        url=url,
        in_catalog=False,
    )
