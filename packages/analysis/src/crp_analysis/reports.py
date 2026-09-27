"""Scan exports: the platform's JSON document (own JSON Schema) and SARIF 2.1.0.

The SARIF log is derived from the JSON export so both carry identical facts. Neither contains
source text. Engines that did not run appear with ``executionSuccessful: false`` and their
reason, so an absent result is never mistaken for a clean one.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any
from urllib.parse import quote

EXPORT_FORMAT = "crp-scan-export/v1"
SARIF_SCHEMA_URI = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
)
_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
_COMPLETED = {"SUCCEEDED", "PARTIAL"}


@cache
def export_schema() -> dict[str, Any]:
    text = (
        resources.files("crp_analysis.schemas")
        .joinpath("crp-scan-export-v1.schema.json")
        .read_text(encoding="utf-8")
    )
    schema: dict[str, Any] = json.loads(text)
    return schema


def build_sarif(export: dict[str, Any]) -> dict[str, Any]:
    by_engine: dict[str, list[dict[str, Any]]] = {}
    for finding in export["findings"]:
        by_engine.setdefault(finding["engine"], []).append(finding)
    runs = []
    for engine in export["engines"]:
        findings = by_engine.get(engine["engine"], [])
        rules: dict[str, dict[str, Any]] = {}
        results = []
        for f in findings:
            if f["rule_id"] not in rules:
                rule: dict[str, Any] = {
                    "id": f["rule_id"],
                    "shortDescription": {"text": f["title"]},
                    "properties": {"category": f["category"]},
                }
                if f["rule_url"]:
                    rule["helpUri"] = f["rule_url"]
                if f["rule_family"]:
                    rule["properties"]["family"] = f["rule_family"]
                rules[f["rule_id"]] = rule
            location: dict[str, Any] = {
                "artifactLocation": {"uri": quote(f["path"], safe="/"), "uriBaseId": "SRCROOT"}
            }
            if f["start_line"] is not None:
                region: dict[str, Any] = {"startLine": f["start_line"]}
                if f["end_line"] is not None:
                    region["endLine"] = f["end_line"]
                if f["start_column"]:
                    region["startColumn"] = f["start_column"]
                if f["end_column"]:
                    region["endColumn"] = f["end_column"]
                location["region"] = region
            properties: dict[str, Any] = {
                "severity": f["severity"],
                "category": f["category"],
                "anchorKind": f["anchor_kind"],
                "engineVersion": f["engine_version"],
            }
            if f["engine_severity"]:
                properties["engineSeverity"] = f["engine_severity"]
            if f["details"]:
                properties["details"] = f["details"]
            if f["issue"]:
                properties["issueStatus"] = f["issue"]["status"]
                properties["recheckState"] = f["issue"]["recheck_state"]
            results.append(
                {
                    "ruleId": f["rule_id"],
                    "ruleIndex": list(rules).index(f["rule_id"]),
                    "level": _LEVEL.get(f["severity"], "warning"),
                    "message": {"text": f["message"] or f["title"]},
                    "locations": [{"physicalLocation": location}],
                    "partialFingerprints": {
                        "crpFingerprint/v1": f["fingerprint"],
                        "crpCorrelation/v1": f["correlation_key"],
                    },
                    "properties": properties,
                }
            )
        invocation: dict[str, Any] = {
            "executionSuccessful": engine["state"] in _COMPLETED,
            "properties": {
                "state": engine["state"],
                "coverage": engine["coverage"],
                "filesEligible": engine["files_eligible"],
                "cacheHits": engine["cache_hits"],
            },
        }
        if engine["exit_code"] is not None:
            invocation["exitCode"] = engine["exit_code"]
        if engine["error_message"] or engine["state"] not in _COMPLETED | {"NOT_APPLICABLE"}:
            invocation["toolExecutionNotifications"] = [
                {
                    "level": "error" if engine["state"] not in _COMPLETED else "warning",
                    "message": {
                        "text": engine["error_message"]
                        or f"{engine['engine']} ended {engine['state']}"
                    },
                }
            ]
        driver: dict[str, Any] = {"name": engine["engine"], "rules": list(rules.values())}
        if engine["version"]:
            driver["version"] = engine["version"]
        runs.append(
            {
                "tool": {"driver": driver},
                "invocations": [invocation],
                "automationDetails": {"id": f"crp/scan/{export['scan']['id']}/{engine['engine']}"},
                "originalUriBaseIds": {
                    "SRCROOT": {"description": {"text": "Root of the frozen source snapshot"}}
                },
                "columnKind": "unicodeCodePoints",
                "results": results,
                "properties": {
                    "snapshotId": export["snapshot"]["id"],
                    "snapshotManifestSha256": export["snapshot"]["manifest_sha256"],
                    "gitCommit": export["snapshot"]["git_commit"],
                    "rulesetId": engine["ruleset_id"],
                    "rulesetSha256": engine["ruleset_sha256"],
                    "limitations": export["limitations"],
                },
            }
        )
    return {"$schema": SARIF_SCHEMA_URI, "version": "2.1.0", "runs": runs}
