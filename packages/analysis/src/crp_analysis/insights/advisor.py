"""The advisor agent (ADR 0022, ADR 0024): an improvement plan for the NFR checkpoints that need
work, grounded in the checkpoint engine's facts and checked deterministically before anyone sees
it.

The agent receives the failing checkpoints and numbered facts (copied from the tools' output) and
may read code with the read-only tools. It submits ordered steps through ``submit_plan``. A step
is kept only if:
- it cites at least one known checkpoint or fact, or a code anchor whose quoted text matches
  the file (unknown ids are dropped and reported);
- every number in its title and rationale appears in what it cites (numbers glued to letters,
  dots or hyphens, such as ``SHA-256`` or ``v2``, are names, not quantities).
The summary may only use numbers that appear in the facts. Checkpoints, priorities and counts
come from the tools; the agent orders, explains and makes steps specific.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from crp_analysis.ai.prompts import SYSTEM, Task
from crp_analysis.ai.results import SubmittedPlan
from crp_analysis.ai.snapshot import SnapshotReader
from crp_analysis.ai.tools import neutralize
from crp_analysis.ai.verify import AnchorStatus, check_anchors

ADVISOR_PROMPT_VERSION = "rx-ai-advisor-v2"
MAX_STEPS = 8
WITHHELD_SUMMARY = (
    "The advisor's summary was withheld because it used numbers that are not in the evidence."
)
_NUMBER = re.compile(r"(?<![\w.-])\d+(?:\.\d+)?(?![\w-])")

ADVISOR_RULES = """

Advisor rules:
8. You plan how to resolve the NFR checkpoints that need work, from the checkpoints and \
numbered facts that refactorX's deterministic tools produced. Checkpoints, priorities and counts \
are fixed by the tools: never invent problems, counts, files, libraries or targets.
9. Each step must cite the checkpoint ids and fact ids (F1, F2, …) it is based on. You may \
read code with the tools to make a step specific; then also cite those lines with the exact \
quoted code.
10. Use only numbers that appear in the facts you cite.
11. Order steps by risk reduction (security and reliability before style), then by effort; at \
most {max_steps} steps. Say what to do, where to start, and how to check it worked.
12. If the facts are too thin for a plan, abstain and say what evidence is missing."""


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text))


def advisor_task(facts: list[dict[str, object]], checkpoints: list[dict[str, object]]) -> Task:
    lines = ["NFR checkpoints that need work (id, area, priority, title, what was found):"]
    for item in checkpoints:
        lines.append(
            f"- {item['id']} | {item['area']} | {item['priority']} | "
            f"{neutralize(str(item['title']))} | {neutralize(str(item['summary']))}"
        )
    lines.append("\nFacts (cite them by id):")
    lines.extend(f"- {f['id']}: {neutralize(str(f['text']))}" for f in facts)
    lines.append("\nCall submit_plan with the improvement plan.")
    system = SYSTEM.format(final_tool="submit_plan") + ADVISOR_RULES.format(max_steps=MAX_STEPS)
    return Task("advisor", "submit_plan", system, "\n".join(lines))


@dataclass(slots=True)
class CheckedStep:
    title: str
    area: str
    rationale: str
    effort: str
    insight_ids: list[str]
    fact_ids: list[str]
    anchors: list[dict[str, object]]  # verified code lines
    dropped_ids: list[str] = field(default_factory=list)  # unknown ids the model cited

    def to_json(self) -> dict[str, object]:
        return {
            "title": self.title,
            "area": self.area,
            "rationale": self.rationale,
            "effort": self.effort,
            "insight_ids": self.insight_ids,
            "fact_ids": self.fact_ids,
            "anchors": self.anchors,
            "dropped_ids": self.dropped_ids,
        }


@dataclass(slots=True)
class CheckedPlan:
    summary: str
    steps: list[CheckedStep]
    rejected: list[dict[str, str]]  # {"title", "reason"}
    abstained: bool
    uncertainty: str

    def to_json(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "steps": [s.to_json() for s in self.steps],
            "rejected": self.rejected,
            "abstained": self.abstained,
            "uncertainty": self.uncertainty,
        }


async def check_plan(
    plan: SubmittedPlan,
    facts: list[dict[str, object]],
    checkpoints: dict[str, str],
    reader: SnapshotReader,
) -> CheckedPlan:
    """``checkpoints``: id -> its title and summary text (what a step may quote)."""
    fact_text = {str(f["id"]): str(f["text"]) for f in facts}
    all_numbers = _numbers(" ".join(fact_text.values()) + " " + " ".join(checkpoints.values()))
    steps: list[CheckedStep] = []
    rejected: list[dict[str, str]] = []
    for step in plan.steps[:MAX_STEPS]:
        insight_ids = [i for i in dict.fromkeys(step.insight_ids) if i in checkpoints]
        fact_ids = [f for f in dict.fromkeys(step.fact_ids) if f in fact_text]
        dropped = [
            i
            for i in [*step.insight_ids, *step.fact_ids]
            if i not in checkpoints and i not in fact_text
        ]
        checks = await check_anchors(reader, step.anchors)
        verified = [
            (anchor, check)
            for anchor, check in zip(step.anchors, checks, strict=True)
            if check.status is AnchorStatus.VERIFIED
        ]
        if not insight_ids and not fact_ids and not verified:
            rejected.append(
                {"title": step.title, "reason": "cites no known checkpoint, fact or code"}
            )
            continue
        cited = " ".join(
            [*(fact_text[f] for f in fact_ids), *(checkpoints[i] for i in insight_ids)]
            + [f"{a.quote} {a.start_line} {a.end_line}" for a, _ in verified]
        )
        unknown = sorted(_numbers(f"{step.title} {step.rationale}") - _numbers(cited))
        if unknown:
            rejected.append(
                {
                    "title": step.title,
                    "reason": f"uses numbers not in the cited evidence: {', '.join(unknown)}",
                }
            )
            continue
        steps.append(
            CheckedStep(
                step.title,
                step.area,
                step.rationale,
                step.effort,
                insight_ids,
                fact_ids,
                [c.as_dict() for _, c in verified],
                dropped,
            )
        )
    summary = plan.summary
    if _numbers(summary) - all_numbers:
        rejected.append(
            {"title": "Summary", "reason": "used numbers that are not in the evidence; withheld"}
        )
        summary = WITHHELD_SUMMARY
    return CheckedPlan(summary, steps, rejected, plan.abstained, plan.uncertainty)
