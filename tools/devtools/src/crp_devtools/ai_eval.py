"""Labelled evaluation of AI file review (``crp-dev ai-eval``).

Scoring criteria are fixed in fixtures/ai-eval/README.md.
Each case runs through the production pipeline pieces (deterministic planner, bounded
investigation with the read-only tools, deterministic anchor verification) over an in-memory
copy of the case's synthetic files. Without ``--live`` the model is the labelled fake test
provider, which checks plumbing and scoring only; its numbers are not model quality. With
``--live`` the configured provider (``CRP_AI_*``) reviews the synthetic files.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from crp_analysis.ai.config import AiSetup, Limits, Prices
from crp_analysis.ai.orchestrator import investigate
from crp_analysis.ai.prompts import PROMPT_VERSION, files_task
from crp_analysis.ai.providers import ClientOptions, ModelClient, OpenAICompatibleClient
from crp_analysis.ai.snapshot import InMemorySnapshot
from crp_analysis.ai.tools import ToolExecutor
from crp_analysis.ai.verify import AnchorStatus, check_anchors, evidence_class
from crp_core.domain.states import AiEvidenceClass
from crp_devtools.testing import fake_ai

SPLITS = ("dev", "held_out")
SLACK_LINES = 2


@dataclass(frozen=True, slots=True)
class Label:
    path: str
    start_line: int
    end_line: int
    category: str
    defect: str


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    split: str
    language: str
    review_paths: tuple[str, ...]
    defective: bool
    labels: tuple[Label, ...]
    files: dict[str, str]


@dataclass(frozen=True, slots=True)
class Anchor:
    path: str
    start_line: int
    end_line: int
    status: str


@dataclass(frozen=True, slots=True)
class Reported:
    title: str
    category: str
    evidence_class: str
    anchors: tuple[Anchor, ...]


@dataclass
class CaseResult:
    case_id: str
    split: str
    defective: bool
    stop: str
    findings: list[Reported] = field(default_factory=list)
    matched_labels: int = 0
    labels: int = 0
    true_positives: int = 0
    reported: int = 0
    rejected: int = 0
    category_agreements: int = 0
    anchors: int = 0
    verified_anchors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage_reported: bool = True
    cost_usd: float | None = None
    error: str | None = None


def load_cases(root: Path, splits: tuple[str, ...] = SPLITS) -> list[Case]:
    cases: list[Case] = []
    for split in splits:
        for meta_file in sorted((root / split).glob("*/case.json")):
            meta = json.loads(meta_file.read_text())
            base = meta_file.parent / "files"
            files = {
                str(path.relative_to(base)): path.read_text()
                for path in sorted(base.rglob("*"))
                if path.is_file()
            }
            cases.append(
                Case(
                    id=meta["id"],
                    split=meta["split"],
                    language=meta["language"],
                    review_paths=tuple(meta["review_paths"]),
                    defective=meta["defective"],
                    labels=tuple(Label(**label) for label in meta["labels"]),
                    files=files,
                )
            )
    return cases


def matches(finding: Reported, label: Label, slack: int = SLACK_LINES) -> bool:
    """Criterion 2: an anchor in the label's file overlapping its range widened by ``slack``."""
    low, high = label.start_line - slack, label.end_line + slack
    return any(
        anchor.path == label.path and anchor.start_line <= high and anchor.end_line >= low
        for anchor in finding.anchors
    )


def score(case: Case, findings: list[Reported], result: CaseResult) -> CaseResult:
    kept = [f for f in findings if f.evidence_class != AiEvidenceClass.REJECTED.value]
    result.findings = findings
    result.labels = len(case.labels)
    result.rejected = len(findings) - len(kept)
    result.reported = len(kept)
    result.true_positives = sum(1 for f in kept if any(matches(f, lb) for lb in case.labels))
    result.matched_labels = sum(1 for lb in case.labels if any(matches(f, lb) for f in kept))
    result.category_agreements = sum(
        1 for f in kept for lb in case.labels if matches(f, lb) and f.category == lb.category
    )
    result.anchors = sum(len(f.anchors) for f in findings)
    result.verified_anchors = sum(
        1 for f in findings for a in f.anchors if a.status == AnchorStatus.VERIFIED.value
    )
    return result


async def run_case(case: Case, client: ModelClient, limits: Limits, prices: Prices) -> CaseResult:
    snapshot = InMemorySnapshot(f"eval-{case.id}", dict(case.files))
    task = await files_task(snapshot, list(case.review_paths))
    outcome = await investigate(
        client, task, ToolExecutor(snapshot), limits, prices, keep_transcript=False
    )
    findings: list[Reported] = []
    for item in outcome.review.findings if outcome.review else []:
        checks = await check_anchors(snapshot, item.anchors)
        findings.append(
            Reported(
                title=item.title,
                category=item.category.value,
                evidence_class=evidence_class(checks).value,
                anchors=tuple(
                    Anchor(c.path, c.start_line, c.end_line, c.status.value) for c in checks
                ),
            )
        )
    usage = outcome.usage
    result = CaseResult(
        case_id=case.id,
        split=case.split,
        defective=case.defective,
        stop=outcome.stop,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        usage_reported=usage.reported,
        cost_usd=prices.cost(usage.input_tokens, usage.output_tokens),
        error=str(outcome.error) if outcome.error else outcome.limitation,
    )
    return score(case, findings, result)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for split in SPLITS:
        rows = [r for r in results if r.split == split]
        if not rows:
            continue
        costs = [r.cost_usd for r in rows if r.cost_usd is not None]
        summary[split] = {
            "cases": len(rows),
            "completed": sum(1 for r in rows if r.stop == "submitted"),
            "precision": _ratio(sum(r.true_positives for r in rows), sum(r.reported for r in rows)),
            "labelled_recall": _ratio(
                sum(r.matched_labels for r in rows), sum(r.labels for r in rows)
            ),
            "false_alarms_on_clean": sum(r.reported for r in rows if not r.defective),
            "rejected_findings": sum(r.rejected for r in rows),
            "category_agreements": sum(r.category_agreements for r in rows),
            "anchor_validity": _ratio(
                sum(r.verified_anchors for r in rows), sum(r.anchors for r in rows)
            ),
            "input_tokens": sum(r.input_tokens for r in rows),
            "output_tokens": sum(r.output_tokens for r in rows),
            "usage_estimated": any(not r.usage_reported for r in rows),
            "cost_usd": round(sum(costs), 6) if len(costs) == len(rows) else None,
        }
    return summary


def offline_client() -> ModelClient:
    """The labelled fake test provider behind an in-process transport (no network)."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fake_ai.respond(json.loads(request.content)))

    return OpenAICompatibleClient(
        api_key="offline-fake-provider",
        model=fake_ai.MODEL,
        base_url="https://fake-provider.invalid/v1",
        options=ClientOptions(timeout_seconds=30, max_retries=0),
        transport=httpx.MockTransport(handle),
    )


async def evaluate(root: Path, splits: tuple[str, ...], setup: AiSetup | None) -> dict[str, Any]:
    """Evaluate ``splits``; ``setup`` None means offline (fake provider, pipeline check only)."""
    live = setup is not None
    client = setup.client() if setup is not None else offline_client()
    limits = (
        setup.limits
        if setup is not None
        else Limits(
            max_model_calls=6,
            max_tool_calls=10,
            max_tokens=100_000,
            timeout_seconds=120,
            max_output_tokens=2048,
            max_cost_usd=None,
        )
    )
    prices = setup.prices if setup is not None else Prices(None, None)
    results = [await run_case(case, client, limits, prices) for case in load_cases(root, splits)]
    return {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "mode": "live" if live else "offline-fake-provider (pipeline check, not model quality)",
        "provider": setup.provider.value if setup is not None else "fake",
        "model": setup.model if setup is not None else fake_ai.MODEL,
        "prompt_version": PROMPT_VERSION,
        "criteria": "fixtures/ai-eval/README.md",
        "summary": summarize(results),
        "cases": [asdict(r) for r in results],
    }


def write_report(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = "live" if report["mode"] == "live" else "offline"
    out = out_dir / f"ai-eval-{kind}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    return out
