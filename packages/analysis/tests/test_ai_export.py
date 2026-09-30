"""AI run exports: JSON validated against its own schema, SARIF against the official 2.1.0 schema;
rejected suggestions never become SARIF results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft4Validator, Draft202012Validator

from crp_analysis.ai.export import ai_export_schema, build_ai_export, build_ai_sarif

SARIF_SCHEMA = json.loads(
    (Path(__file__).parent / "data" / "sarif-schema-2.1.0.json").read_text(encoding="utf-8")
)
SHA = "a" * 64
RUN_ID = "3f2c8f1e-0000-4000-8000-000000000001"
FINDING_ID = "3f2c8f1e-0000-4000-8000-000000000002"


def _anchor(status: str, line: int = 12, path: str = "src/orders.py") -> dict[str, Any]:
    return {
        "path": path,
        "start_line": line,
        "end_line": line,
        "quote": "return total - total * percent / 10",
        "status": status,
        "sha256": None if status == "unknown_path" else SHA,
    }


def _finding(number: int, evidence: str, anchors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": f"3f2c8f1e-0000-4000-8000-00000000010{number}",
        "title": f"Finding {number}",
        "category": "correctness",
        "severity": "high",
        "severity_rationale": "r",
        "confidence": "medium",
        "evidence_class": evidence,
        "anchors": anchors,
        "triggering_conditions": "t",
        "impact": "i",
        "recommendation": "fix",
        "validation_needed": None,
        "uncertainty": None,
        "related_finding_id": None,
    }


def _run() -> dict[str, Any]:
    return {
        "id": RUN_ID,
        "project_id": "3f2c8f1e-0000-4000-8000-000000000003",
        "snapshot_id": "3f2c8f1e-0000-4000-8000-000000000004",
        "scan_id": None,
        "finding_id": FINDING_ID,
        "kind": "finding_review",
        "state": "SUCCEEDED",
        "question": None,
        "target_paths": None,
        "provider": "anthropic",
        "model": "claude-opus-5-5",
        "prompt_version": "rx-ai-v1",
        "created_at": "2026-09-30T10:00:00Z",
        "started_at": "2026-09-30T10:00:01Z",
        "finished_at": "2026-09-30T10:00:09Z",
        "cancel_requested_at": None,
        "error_code": None,
        "error_message": None,
        "usage": {
            "calls": 2,
            "input_tokens": 900,
            "output_tokens": 120,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "usage_reported": True,
            "budget_tokens": 1020,
            "cost_usd": None,
            "excerpts": 1,
        },
        "answer": {
            "type": "review",
            "text": "summary",
            "abstained": False,
            "uncertainty": "",
            "inferred_intent": "",
            "evidence_class": None,
            "citations": [],
            "reviewed_paths": ["src/orders.py"],
            "assessment": {
                "verdict": "confirmed",
                "explanation": "The divisor is wrong.",
                "evidence_class": "verified_anchor",
                "anchors": [_anchor("verified")],
            },
        },
        "steps": [],
        "limitations": ["Source-only AI review."],
        "findings": [
            _finding(1, "verified_anchor", [_anchor("verified"), _anchor("verified", 13)]),
            _finding(2, "hypothesis", [_anchor("unknown_path", path="src/ghost.py")]),
            _finding(3, "rejected", [_anchor("quote_mismatch", 3)]),
        ],
    }


def _export() -> dict[str, Any]:
    return build_ai_export(
        _run(),
        project={"id": "3f2c8f1e-0000-4000-8000-000000000003", "name": "Shop"},
        snapshot={"id": "3f2c8f1e-0000-4000-8000-000000000004", "manifest_sha256": SHA},
        tool_version="0.1.0",
        generated_at="2026-09-30T10:01:00+00:00",
    )


def test_json_export_matches_its_schema_and_keeps_rejected_suggestions_marked() -> None:
    schema = ai_export_schema()
    Draft202012Validator.check_schema(schema)
    export = _export()
    assert list(Draft202012Validator(schema).iter_errors(export)) == []
    assert [f["evidence_class"] for f in export["findings"]] == [
        "verified_anchor",
        "hypothesis",
        "rejected",
    ]
    assert "does not prove the conclusion" in export["notice"]
    broken = {**export, "extra": 1}
    assert list(Draft202012Validator(schema).iter_errors(broken))


def test_sarif_is_valid_and_excludes_rejected_suggestions() -> None:
    sarif = build_ai_sarif(_export())
    errors = sorted(Draft4Validator(SARIF_SCHEMA).iter_errors(sarif), key=lambda e: list(e.path))
    assert errors == [], errors[0].message if errors else ""
    [run] = sarif["runs"]
    results = run["results"]
    finding_ids = [r["properties"].get("findingId") for r in results]
    assert "3f2c8f1e-0000-4000-8000-000000000103" not in finding_ids  # rejected
    verified = results[0]
    assert verified["properties"]["evidenceClass"] == "verified_anchor"
    assert verified["properties"]["aiGenerated"] is True
    assert verified["locations"][0]["physicalLocation"]["region"]["startLine"] == 12
    assert verified["relatedLocations"][0]["physicalLocation"]["region"]["startLine"] == 13
    hypothesis = results[1]
    assert "locations" not in hypothesis  # its only anchor names a file outside the upload
    assessment = results[-1]
    assert assessment["ruleId"] == "ai/finding-assessment"
    assert assessment["properties"]["assessedFindingId"] == FINDING_ID
    assert run["tool"]["driver"]["name"] == "refactorX AI review"
    assert run["properties"]["model"] == "claude-opus-5-5"
