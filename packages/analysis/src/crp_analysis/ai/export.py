"""AI run exports: the run as JSON (own JSON Schema) and its AI findings as SARIF 2.1.0.

AI output is kept apart from scan exports. Scan exports carry deterministic facts that later scans
can re-check; these carry model output together with its evidence class. Rejected suggestions (no
citation matched the code) stay in the JSON, marked, but never become SARIF results. The only
source text included is what the model quoted in its citations, which was masked before sending.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any
from urllib.parse import quote

from crp_analysis.reports import SARIF_SCHEMA_URI

EXPORT_FORMAT = "crp-ai-run-export/v1"
NOTICE = (
    "AI-generated review output. 'verified_anchor' means every cited line exists in the upload "
    "with the quoted code; it does not prove the conclusion. 'hypothesis' was not fully "
    "verified; 'rejected' matched no code. Deterministic scan findings are exported separately."
)
_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
_LOCATABLE = {"verified", "unquoted", "quote_mismatch"}  # the cited file and lines exist
_COMPLETED = {"SUCCEEDED", "PARTIAL"}


@cache
def ai_export_schema() -> dict[str, Any]:
    text = (
        resources.files("crp_analysis.schemas")
        .joinpath("crp-ai-run-export-v1.schema.json")
        .read_text(encoding="utf-8")
    )
    schema: dict[str, Any] = json.loads(text)
    return schema


def build_ai_export(
    run: dict[str, Any],
    *,
    project: dict[str, Any],
    snapshot: dict[str, Any],
    tool_version: str,
    generated_at: str,
) -> dict[str, Any]:
    """``run`` is the API representation of the run (``AiRunResponse`` as JSON)."""
    return {
        "format": EXPORT_FORMAT,
        "generated_at": generated_at,
        "tool": {"name": "refactorX", "component": "ai-review", "version": tool_version},
        "project": project,
        "snapshot": snapshot,
        "run": {
            key: run[key]
            for key in (
                "id",
                "kind",
                "state",
                "question",
                "target_paths",
                "finding_id",
                "scan_id",
                "provider",
                "model",
                "prompt_version",
                "created_at",
                "started_at",
                "finished_at",
                "error_code",
                "error_message",
            )
        },
        "usage": run["usage"],
        "answer": run["answer"],
        "findings": run["findings"],
        "limitations": run["limitations"],
        "notice": NOTICE,
    }


def _location(anchor: dict[str, Any]) -> dict[str, Any]:
    return {
        "physicalLocation": {
            "artifactLocation": {"uri": quote(anchor["path"], safe="/"), "uriBaseId": "SRCROOT"},
            "region": {"startLine": anchor["start_line"], "endLine": anchor["end_line"]},
        }
    }


def _result(
    rule_id: str,
    rules: list[str],
    level: str,
    text: str,
    anchors: list[dict[str, Any]],
    properties: dict[str, Any],
) -> dict[str, Any]:
    located = [a for a in anchors if a["status"] in _LOCATABLE]
    result: dict[str, Any] = {
        "ruleId": rule_id,
        "ruleIndex": rules.index(rule_id),
        "level": level,
        "message": {"text": text},
        "properties": {**properties, "aiGenerated": True},
    }
    if located:
        result["locations"] = [_location(located[0])]
        if len(located) > 1:
            result["relatedLocations"] = [
                {"id": index, **_location(anchor)} for index, anchor in enumerate(located[1:])
            ]
    return result


def build_ai_sarif(export: dict[str, Any]) -> dict[str, Any]:
    run = export["run"]
    rule_ids: list[str] = []
    rule_defs: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    def rule(rule_id: str, description: str) -> str:
        if rule_id not in rule_ids:
            rule_ids.append(rule_id)
            rule_defs.append({"id": rule_id, "shortDescription": {"text": description}})
        return rule_id

    for finding in export["findings"]:
        if finding["evidence_class"] == "rejected":
            continue
        rule_id = rule(f"ai/{finding['category']}", f"AI review: {finding['category']}")
        results.append(
            _result(
                rule_id,
                rule_ids,
                _LEVEL.get(finding["severity"], "warning"),
                f"{finding['title']}. {finding['impact']}",
                finding["anchors"],
                {
                    "severity": finding["severity"],
                    "confidence": finding["confidence"],
                    "evidenceClass": finding["evidence_class"],
                    "recommendation": finding["recommendation"],
                    "findingId": finding["id"],
                },
            )
        )
    answer = export["answer"] or {}
    assessment = answer.get("assessment")
    if assessment and assessment["evidence_class"] != "rejected":
        rule_id = rule("ai/finding-assessment", "AI second opinion on a deterministic finding")
        results.append(
            _result(
                rule_id,
                rule_ids,
                "note",
                f"{assessment['verdict'].replace('_', ' ')}: {assessment['explanation']}",
                assessment["anchors"],
                {
                    "verdict": assessment["verdict"],
                    "evidenceClass": assessment["evidence_class"],
                    "assessedFindingId": run["finding_id"],
                },
            )
        )
    invocation: dict[str, Any] = {
        "executionSuccessful": run["state"] in _COMPLETED,
        "properties": {"state": run["state"]},
    }
    if run["error_code"]:
        invocation["toolExecutionNotifications"] = [
            {
                "level": "error",
                "message": {"text": run["error_message"] or run["error_code"]},
            }
        ]
    return {
        "$schema": SARIF_SCHEMA_URI,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "refactorX AI review",
                        "version": run["prompt_version"],
                        "rules": rule_defs,
                    }
                },
                "invocations": [invocation],
                "automationDetails": {"id": f"crp/ai-run/{run['id']}"},
                "originalUriBaseIds": {
                    "SRCROOT": {"description": {"text": "Root of the frozen source snapshot"}}
                },
                "columnKind": "unicodeCodePoints",
                "results": results,
                "properties": {
                    "snapshotId": export["snapshot"]["id"],
                    "snapshotManifestSha256": export["snapshot"]["manifest_sha256"],
                    "provider": run["provider"],
                    "model": run["model"],
                    "promptVersion": run["prompt_version"],
                    "notice": export["notice"],
                },
            }
        ],
    }
