"""NFR readiness per question and aspect (P12 slice 1; docs/NFR_ASSESSMENT.md).

Each question combines what the upload shows (signals), what needs work (open tracked issues
mapped to it), and what the team stated (profile). Its status, in order of precedence:

1. ``not_applicable`` — the team declared it, with a reason;
2. ``needs_work`` — open issues are gaps for it;
3. ``needs_input`` — only the team can answer it and has not;
4. ``evidence`` — a supporting mechanism is declared or present;
5. ``answered`` — the team answered it;
6. ``not_checked`` — nothing either way. Never shown as fine.

Measured answers (imported reports) come with a later slice.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field

from crp_analysis.nfr.profile import TARGETS, Answer, Profile
from crp_analysis.nfr.questionnaire import Aspect, Question, Questionnaire, questions_for_rule
from crp_analysis.nfr.signals import Evidence

STATUSES = ("needs_work", "needs_input", "evidence", "answered", "not_applicable", "not_checked")
STATUS_LABELS = {
    "needs_work": "Needs work",
    "needs_input": "Needs your input",
    "evidence": "Evidence found",
    "answered": "Answered by your team",
    "not_applicable": "Not applicable",
    "not_checked": "Not checked yet",
}
OPEN = frozenset({"OPEN", "TRIAGED", "FIX_PROPOSED"})
ACCEPTED = "ACCEPTED_RISK"
_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
TOP_ISSUES = 5


@dataclass(frozen=True, slots=True)
class TrackedIssue:
    id: str
    engine: str
    rule_id: str
    category: str
    family: str | None
    severity: str
    title: str
    path: str
    status: str


@dataclass(slots=True)
class Gaps:
    open: int = 0
    accepted: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)
    top: list[TrackedIssue] = field(default_factory=list)


@dataclass(slots=True)
class QuestionResult:
    question: Question
    status: str
    evidence: list[Evidence]  # supports
    context: list[Evidence]  # in the upload, not assessed yet
    gaps: Gaps
    answer: Answer | None
    values: dict[str, object]  # the team's targets and lists for this question


@dataclass(slots=True)
class AspectResult:
    aspect: Aspect
    questions: list[QuestionResult]
    counts: dict[str, int]


@dataclass(slots=True)
class Assessment:
    aspects: list[AspectResult]
    counts: dict[str, int]
    reviewed: bool  # an upload has been reviewed (otherwise there is no code evidence)


def _gaps(issues: list[TrackedIssue]) -> Gaps:
    gaps = Gaps()
    open_issues = [i for i in issues if i.status in OPEN]
    gaps.open = len(open_issues)
    gaps.accepted = sum(1 for i in issues if i.status == ACCEPTED)
    gaps.by_severity = dict(Counter(i.severity for i in open_issues))
    gaps.top = sorted(open_issues, key=lambda i: (_SEVERITY.get(i.severity, 9), i.path, i.title))[
        :TOP_ISSUES
    ]
    return gaps


def _status(
    question: Question,
    result_gaps: Gaps,
    evidence: list[Evidence],
    answer: Answer | None,
    values: dict[str, object],
) -> str:
    if answer is not None and answer.not_applicable:
        return "not_applicable"
    if result_gaps.open:
        return "needs_work"
    team = bool((answer and answer.text) or values)
    if question.team_required and not team:
        return "needs_input"
    if evidence:
        return "evidence"
    if team:
        return "answered"
    return "not_checked"


def assess(
    questionnaire: Questionnaire,
    evidence: list[Evidence],
    issues: list[TrackedIssue],
    profile: Profile,
    *,
    reviewed: bool,
) -> Assessment:
    by_question: dict[str, list[TrackedIssue]] = {q.id: [] for q in questionnaire.questions}
    for issue in issues:
        for qid in questions_for_rule(issue.engine, issue.rule_id, issue.category, issue.family):
            if qid in by_question:
                by_question[qid].append(issue)
    aspects: list[AspectResult] = []
    total: Counter[str] = Counter()
    for aspect in questionnaire.aspects:
        results: list[QuestionResult] = []
        for question in (q for q in questionnaire.questions if q.aspect == aspect.id):
            supports = [e for e in evidence if e.kind == "supports" and question.id in e.questions]
            context = [e for e in evidence if e.kind == "context" and question.id in e.questions]
            gaps = _gaps(by_question[question.id])
            answer = profile.answers.get(question.id)
            values = profile.values_for(question.profile_fields)
            status = _status(question, gaps, supports, answer, values)
            results.append(
                QuestionResult(question, status, supports, context, gaps, answer, values)
            )
        counts = Counter(r.status for r in results)
        total.update(counts)
        aspects.append(AspectResult(aspect, results, {s: counts.get(s, 0) for s in STATUSES}))
    return Assessment(aspects, {s: total.get(s, 0) for s in STATUSES}, reviewed)


# -- exports ----------------------------------------------------------------------------------

_LABEL = {t.name: (t.label, t.unit) for t in TARGETS}
_LABEL.update({"regulations": ("Regulations", ""), "platforms": ("Platforms", "")})


def _value(name: str, value: object) -> str:
    label, unit = _LABEL.get(name, (name, ""))
    shown = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
    return f"{label}: {shown}" + (f" {unit}" if unit else "")


def _evidence_text(items: list[Evidence]) -> str:
    parts = []
    for item in items:
        where = ", ".join(
            f"{path}{f':{line}' if line else ''}" for path, line, _ in item.locations[:2]
        )
        more = f" (+{item.count - 2})" if item.count > 2 else ""
        parts.append(f"{item.label} ({where}{more})")
    return "; ".join(parts)


def to_csv(assessment: Assessment) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "Aspect",
            "ISO/IEC 25010:2023",
            "Question",
            "Status",
            "Evidence in the code",
            "In the upload, not checked yet",
            "Open issues",
            "Accepted risks",
            "Team answer",
            "Team targets and inputs",
            "Not applicable because",
        ]
    )
    for aspect in assessment.aspects:
        for result in aspect.questions:
            writer.writerow(
                [
                    aspect.aspect.name,
                    aspect.aspect.iso,
                    result.question.text,
                    STATUS_LABELS[result.status],
                    _evidence_text(result.evidence),
                    _evidence_text(result.context),
                    result.gaps.open,
                    result.gaps.accepted,
                    result.answer.text if result.answer and result.answer.text else "",
                    "; ".join(_value(k, v) for k, v in result.values.items()),
                    result.answer.reason if result.answer and result.answer.not_applicable else "",
                ]
            )
    return out.getvalue()


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(assessment: Assessment, *, project: str, source: str, basis: str) -> str:
    lines = [
        f"# NFR readiness: {_md(project)}",
        "",
        f"{basis}",
        "",
        "Statuses: **Needs work** (open issues are gaps), **Needs your input** (only your team "
        "can answer), **Evidence found** (the upload declares or contains a supporting mechanism; "
        "this shows intent, not run-time behaviour), **Answered by your team** (attested, not "
        "detected), **Not applicable** (declared with a reason), **Not checked yet** (nothing "
        "either way; never read as fine). refactorX does not certify compliance.",
        "",
        "| Aspect | " + " | ".join(STATUS_LABELS[s] for s in STATUSES) + " |",
        "|---|" + "---|" * len(STATUSES),
    ]
    for aspect in assessment.aspects:
        lines.append(
            f"| {_md(aspect.aspect.name)} | "
            + " | ".join(str(aspect.counts[s]) for s in STATUSES)
            + " |"
        )
    for aspect in assessment.aspects:
        lines += ["", f"## {_md(aspect.aspect.name)} ({_md(aspect.aspect.iso)})"]
        for result in aspect.questions:
            lines += [
                "",
                f"### {_md(result.question.text)}",
                "",
                f"Status: **{STATUS_LABELS[result.status]}**.",
            ]
            if result.evidence:
                lines.append(f"- Evidence in the code: {_md(_evidence_text(result.evidence))}.")
            if result.context:
                lines.append(
                    f"- In the upload, not checked yet: {_md(_evidence_text(result.context))}."
                )
            if result.gaps.open or result.gaps.accepted:
                worst = ", ".join(
                    f"{_md(i.title)} ({i.severity}, {_md(i.path)})" for i in result.gaps.top
                )
                lines.append(
                    f"- Open issues: {result.gaps.open}"
                    + (f"; accepted risks: {result.gaps.accepted}" if result.gaps.accepted else "")
                    + (f". Most severe: {worst}." if worst else ".")
                )
            if result.values:
                lines.append(
                    "- Team targets and inputs: "
                    + _md("; ".join(_value(k, v) for k, v in result.values.items()))
                    + "."
                )
            if result.answer and result.answer.text:
                lines.append(f"- Team answer (attested): {_md(result.answer.text)}")
            if result.answer and result.answer.not_applicable:
                lines.append(f"- Not applicable: {_md(result.answer.reason or '')}")
            if result.status == "not_checked":
                lines.append(f"- {_md(result.question.help)}")
    lines += ["", f"Questionnaire: {_md(source)}.", ""]
    return "\n".join(lines)
