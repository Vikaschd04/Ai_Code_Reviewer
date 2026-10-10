"""A project's NFR profile (P12 slice 1): the team's targets, regulations, platforms and attested
answers to questionnaire questions. Saved as append-only versions (who, when, why), like the
architecture rules (ADR 0019). Everything here is the team's statement, never detected.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from crp_analysis.nfr.questionnaire import load

SCHEMA = "crp-nfr-profile-v1"
MAX_TEXT = 2000
MAX_SHORT = 300
MAX_ITEMS = 20


@dataclass(frozen=True, slots=True)
class Target:
    name: str
    label: str
    kind: str  # int | float | text
    unit: str = ""
    low: float = 0
    high: float = 0


TARGETS: tuple[Target, ...] = (
    Target("availability_percent", "Availability target", "float", "%", 1, 100),
    Target("latency_p95_ms", "Response time target (95th percentile)", "int", "ms", 1, 600_000),
    Target("page_load_seconds", "Page load target", "float", "s", 0.1, 120),
    Target("typical_users", "Typical users", "int", "users", 0, 1_000_000_000),
    Target("peak_concurrent_users", "Peak concurrent users", "int", "users", 0, 1_000_000_000),
    Target("rto_minutes", "Recovery time objective (RTO)", "int", "min", 0, 525_600),
    Target("rpo_minutes", "Recovery point objective (RPO)", "int", "min", 0, 525_600),
    Target("growth", "Projected growth", "text"),
    Target("downtime_cost", "Cost of downtime", "text"),
    Target("accessibility", "Accessibility target", "text"),
)
TARGET_NAMES = frozenset(t.name for t in TARGETS)


class ProfileError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Answer:
    text: str | None = None
    not_applicable: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Profile:
    targets: dict[str, float | int | str] = field(default_factory=dict)
    regulations: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    answers: dict[str, Answer] = field(default_factory=dict)

    def to_document(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "targets": {t.name: self.targets[t.name] for t in TARGETS if t.name in self.targets},
            "regulations": list(self.regulations),
            "platforms": list(self.platforms),
            "answers": {
                qid: {
                    **({"text": a.text} if a.text else {}),
                    **({"not_applicable": True, "reason": a.reason} if a.not_applicable else {}),
                }
                for qid, a in sorted(self.answers.items())
            },
        }

    def sha256(self) -> str:
        text = json.dumps(self.to_document(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def values_for(self, fields: tuple[str, ...]) -> dict[str, object]:
        """The team's statements for these profile fields (targets and lists)."""
        values: dict[str, object] = {}
        for name in fields:
            if name in self.targets:
                values[name] = self.targets[name]
            elif name == "regulations" and self.regulations:
                values[name] = list(self.regulations)
            elif name == "platforms" and self.platforms:
                values[name] = list(self.platforms)
        return values


def _text(value: object, where: str, limit: int, problems: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        problems.append(f"{where}: must be text")
        return None
    text = value.strip()
    if len(text) > limit:
        problems.append(f"{where}: at most {limit} characters")
        return None
    return text or None


def _items(value: object, where: str, problems: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        problems.append(f"{where}: a list of at most {MAX_ITEMS} entries")
        return ()
    items = [_text(v, f"{where}[{i + 1}]", 100, problems) for i, v in enumerate(value)]
    return tuple(dict.fromkeys(i for i in items if i))


def from_document(data: object) -> Profile:
    """Validate a profile document. Raises ``ProfileError`` listing every problem."""
    if data is None:
        return Profile()
    if not isinstance(data, dict):
        raise ProfileError(["the profile must be a mapping"])
    problems: list[str] = []
    unknown = sorted(
        str(k)
        for k in data
        if k not in {"schema", "targets", "regulations", "platforms", "answers"}
    )
    if unknown:
        problems.append(f"profile: unknown field {', '.join(unknown)}")
    if data.get("schema", SCHEMA) != SCHEMA:
        problems.append(f"schema: must be {SCHEMA}")
    targets: dict[str, float | int | str] = {}
    raw_targets = data.get("targets") or {}
    if not isinstance(raw_targets, dict):
        problems.append("targets: must be a mapping")
        raw_targets = {}
    for name, value in raw_targets.items():
        spec = next((t for t in TARGETS if t.name == name), None)
        where = f"targets.{name}"
        if spec is None:
            problems.append(f"{where}: unknown target")
            continue
        if value is None:
            continue
        if spec.kind == "text":
            text = _text(value, where, MAX_SHORT, problems)
            if text:
                targets[name] = text
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            problems.append(f"{where}: must be a number")
            continue
        if spec.kind == "int" and not float(value).is_integer():
            problems.append(f"{where}: must be a whole number")
            continue
        if not spec.low <= value <= spec.high:
            problems.append(f"{where}: between {spec.low:g} and {spec.high:g} {spec.unit}".strip())
            continue
        targets[name] = int(value) if spec.kind == "int" else float(value)
    answers: dict[str, Answer] = {}
    raw_answers = data.get("answers") or {}
    if not isinstance(raw_answers, dict):
        problems.append("answers: must be a mapping of question ids")
        raw_answers = {}
    known = load().ids
    for qid, raw in raw_answers.items():
        where = f"answers.{qid}"
        if qid not in known:
            problems.append(f"{where}: unknown question")
            continue
        if not isinstance(raw, dict):
            problems.append(f"{where}: must be a mapping with text or not_applicable")
            continue
        extra = sorted(str(k) for k in raw if k not in {"text", "not_applicable", "reason"})
        if extra:
            problems.append(f"{where}: unknown field {', '.join(extra)}")
        text = _text(raw.get("text"), f"{where}.text", MAX_TEXT, problems)
        not_applicable = raw.get("not_applicable") is True
        reason = _text(raw.get("reason"), f"{where}.reason", MAX_SHORT, problems)
        if not_applicable and not reason:
            problems.append(f"{where}.reason: say why the question does not apply")
            continue
        if text or not_applicable:
            answers[qid] = Answer(text, not_applicable, reason if not_applicable else None)
    profile = Profile(
        targets,
        _items(data.get("regulations"), "regulations", problems),
        _items(data.get("platforms"), "platforms", problems),
        answers,
    )
    if problems:
        raise ProfileError(problems[:50])
    return profile
