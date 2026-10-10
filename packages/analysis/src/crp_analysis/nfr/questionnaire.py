"""The built-in NFR questionnaire and how tracked issues map onto its questions (P12 slice 1).

The questions are the owner-supplied list (9 aspects, 23 questions, quoted verbatim) mapped to
ISO/IEC 25010:2023 characteristics. An open issue is a gap for the questions its rule speaks to,
decided from the rule catalog's category and family; the mapping is deliberately conservative
(a rule maps to the questions it directly answers).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources

SCHEMA = "crp-nfr-questionnaire-v1"
MAPPING_VERSION = "crp-nfr-mapping-v1"


@dataclass(frozen=True, slots=True)
class Aspect:
    id: str
    name: str
    iso: str


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    aspect: str
    text: str
    help: str
    team_required: bool
    profile_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Questionnaire:
    source: str
    aspects: tuple[Aspect, ...]
    questions: tuple[Question, ...]

    def question(self, question_id: str) -> Question | None:
        return next((q for q in self.questions if q.id == question_id), None)

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(q.id for q in self.questions)


@cache
def load() -> Questionnaire:
    raw = json.loads(
        resources.files("crp_analysis.nfr").joinpath("questionnaire.json").read_text("utf-8")
    )
    if raw.get("schema") != SCHEMA:
        raise ValueError(f"questionnaire must use schema {SCHEMA}")
    return Questionnaire(
        source=str(raw["source"]),
        aspects=tuple(Aspect(a["id"], a["name"], a["iso"]) for a in raw["aspects"]),
        questions=tuple(
            Question(
                q["id"],
                q["aspect"],
                q["text"],
                q["help"],
                bool(q["team_required"]),
                tuple(q["profile_fields"]),
            )
            for q in raw["questions"]
        ),
    )


# -- tracked issues as gaps -------------------------------------------------------------------

_ACCESS_FAMILIES = frozenset(
    {
        "secrets.credentials",
        "secrets.hardcoded-token-secret",
        "secrets.logging",
        "crypto.hardcoded-key",
        "transport.insecure",
    }
)
_ACCESS_RULES = frozenset(
    {("pmd-apex", "ApexCRUDViolation"), ("pmd-apex", "ApexSharingViolations")}
)


def questions_for_rule(
    engine: str, rule_id: str, category: str, family: str | None
) -> tuple[str, ...]:
    """The questions an open issue of this rule is a gap for (empty: none)."""
    if (
        (engine == "trivy" and rule_id.startswith("secret:"))
        or (engine, rule_id) in _ACCESS_RULES
        or family in _ACCESS_FAMILIES
    ):
        return ("security.access",)
    if category in {"security", "dependencies"}:
        return ("security.attacks",)
    if category == "performance":
        extra = {
            "performance.unbounded-query": ("performance.growth", "scalability.demand"),
            "performance.persistence-in-loop": ("scalability.demand",),
        }.get(family or "", ())
        return ("performance.latency", *extra)
    if family == "compatibility.api-version":
        return ("reliability.consistency", "portability.platforms")
    if category in {"correctness", "reliability"}:
        return ("reliability.consistency",)
    if engine == "smells" and rule_id == "crp.arch.hub":
        return ("operations.remediation", "availability.fault-tolerance")
    if category in {"maintainability", "coding_standards"}:
        return ("operations.remediation",)
    return ()
