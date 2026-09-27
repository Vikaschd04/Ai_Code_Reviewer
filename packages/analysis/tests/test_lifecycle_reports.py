"""Strict recheck classification and export schema validation (JSON export + SARIF 2.1.0)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft4Validator, Draft202012Validator

from crp_analysis.lifecycle import Prior, RunView, classify_absence
from crp_analysis.reports import build_sarif, export_schema
from crp_core.domain.states import RecheckState

PRIOR = Prior("pmd", "EmptyCatchBlock", "src/A.java", "7.17.0", "a" * 64)
OK_RUN = RunView("pmd", "SUCCEEDED", "7.17.0", "a" * 64, frozenset({"EmptyCatchBlock"}))


@pytest.mark.parametrize(
    ("run", "present", "outcome", "expected"),
    [
        (None, True, None, RecheckState.NOT_RECHECKED),
        (RunView("pmd", "FAILED", "7.17.0", "a" * 64, None), True, None, RecheckState.NOT_RECHECKED),
        (RunView("pmd", "UNAVAILABLE", None, None, None), True, None, RecheckState.NOT_RECHECKED),
        (OK_RUN, False, None, RecheckState.UNKNOWN),  # deleted / renamed / excluded
        (RunView("pmd", "SUCCEEDED", "7.17.0", "b" * 64, frozenset({"Other"})), True, "ANALYZED",
         RecheckState.RULE_OBSOLETE),
        (OK_RUN, True, "FAILED", RecheckState.NOT_RECHECKED),  # partial coverage
        (OK_RUN, True, "NOT_ATTEMPTED", RecheckState.NOT_RECHECKED),
        (RunView("pmd", "SUCCEEDED", "7.18.0", "a" * 64, frozenset({"EmptyCatchBlock"})), True,
         "ANALYZED", RecheckState.UNKNOWN),  # engine version changed
        (RunView("pmd", "PARTIAL", "7.17.0", "c" * 64, frozenset({"EmptyCatchBlock"})), True,
         "ANALYZED", RecheckState.UNKNOWN),  # rules/config changed
        (OK_RUN, True, "ANALYZED", RecheckState.VERIFIED_ABSENT),
        (RunView("trivy", "SUCCEEDED", "7.17.0", "a" * 64, None), True, "ANALYZED",
         RecheckState.VERIFIED_ABSENT),  # open-ended rule set: cannot be "obsolete"
    ],
)  # fmt: skip
def test_absence_is_verified_only_by_a_compatible_complete_recheck(
    run: RunView | None, present: bool, outcome: str | None, expected: RecheckState
) -> None:
    result = classify_absence(PRIOR, run, file_present=present, file_outcome=outcome)
    assert result.state is expected
    assert result.reason


def _export() -> dict[str, Any]:
    ids = [str(uuid.uuid4()) for _ in range(6)]
    engine = {
        "engine": "trivy",
        "version": "0.69.3",
        "ruleset_id": "crp-trivy-v1",
        "ruleset_sha256": "d" * 64,
        "state": "SUCCEEDED",
        "files_eligible": 3,
        "files_attempted": 3,
        "files_succeeded": 3,
        "files_failed": 0,
        "findings": 2,
        "coverage": {"ANALYZED": 3, "FAILED": 0, "NOT_ATTEMPTED": 0},
        "cache_hits": 0,
        "cache_misses": 0,
        "duration_ms": 1200,
        "exit_code": 0,
        "error_code": None,
        "error_message": None,
        "raw_report_sha256": "e" * 64,
    }
    unavailable = {
        **engine,
        "engine": "opengrep",
        "version": None,
        "state": "UNAVAILABLE",
        "files_attempted": 0,
        "files_succeeded": 0,
        "findings": 0,
        "coverage": {"ANALYZED": 0, "FAILED": 0, "NOT_ATTEMPTED": 3},
        "exit_code": None,
        "error_code": "engine_unavailable",
        "error_message": "Opengrep binary not found; run make engines",
        "raw_report_sha256": None,
    }
    base_finding = {
        "fingerprint": "1" * 64,
        "correlation_key": "1" * 64,
        "engine": "trivy",
        "engine_version": "0.69.3",
        "ruleset": "crp-trivy-v1",
        "rule_family": None,
        "engine_severity": "CRITICAL",
        "confidence": "deterministic_rule",
        "rule_url": "https://avd.aquasec.com/nvd/cve-2021-44228",
        "issue": None,
    }
    return {
        "format": "crp-scan-export/v1",
        "generated_at": "2026-09-27T10:00:00+00:00",
        "tool": {"name": "Code Review Platform", "version": "0.1.0"},
        "project": {"id": ids[0], "slug": "demo", "name": "Demo"},
        "snapshot": {
            "id": ids[1],
            "manifest_sha256": "f" * 64,
            "manifest_version": 1,
            "file_count": 3,
            "analyzable_count": 3,
            "excluded_count": 0,
            "git_commit": None,
            "frozen_at": "2026-09-27T09:00:00+00:00",
            "policy_version": "scope-v1",
        },
        "scan": {
            "id": ids[2],
            "state": "PARTIAL",
            "mode": "baseline",
            "policy_version": "crp-baseline-v1",
            "cache_mode": "use",
            "created_at": "2026-09-27T09:01:00+00:00",
            "started_at": "2026-09-27T09:01:01+00:00",
            "finished_at": "2026-09-27T09:02:00+00:00",
            "lifecycle_applied": True,
            "summary": {"findings": 2},
        },
        "engines": [engine, unavailable],
        "findings": [
            {
                **base_finding,
                "id": ids[3],
                "rule_id": "CVE-2021-44228",
                "severity": "critical",
                "category": "dependencies",
                "title": "CVE-2021-44228 in org.apache.logging.log4j:log4j-core 2.14.1",
                "message": "log4j-core 2.14.1 is affected",
                "path": "pom.xml",
                "anchor_kind": "dependency",
                "start_line": None,
                "start_column": None,
                "end_line": None,
                "end_column": None,
                "details": {
                    "package": "org.apache.logging.log4j:log4j-core",
                    "fixed_version": "2.15.0",
                },
            },
            {
                **base_finding,
                "id": ids[4],
                "fingerprint": "2" * 64,
                "correlation_key": "2" * 64,
                "rule_id": "secret:github-pat",
                "severity": "critical",
                "category": "security",
                "title": "Exposed secret: GitHub Personal Access Token",
                "message": "Possible GitHub token (value masked; never stored).",
                "path": "web/src/config file.js",
                "anchor_kind": "source_span",
                "start_line": 2,
                "start_column": None,
                "end_line": 2,
                "end_column": None,
                "details": None,
                "issue": {"id": ids[5], "status": "OPEN", "recheck_state": "VERIFIED_PRESENT"},
            },
        ],
        "limitations": ["opengrep: not run (Opengrep binary not found)"],
    }


def _sarif_schema() -> dict[str, Any]:
    path = Path(__file__).parent / "data" / "sarif-schema-2.1.0.json"
    schema: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return schema


def test_export_schema_accepts_a_complete_document_and_rejects_invented_lines() -> None:
    validator = Draft202012Validator(export_schema())
    Draft202012Validator.check_schema(export_schema())
    document = _export()
    validator.validate(document)
    broken = _export()
    broken["findings"][1]["start_line"] = None  # a source span without a line is invalid
    assert list(validator.iter_errors(broken))
    broken = _export()
    broken["findings"][0]["severity"] = "urgent"
    assert list(validator.iter_errors(broken))


def test_sarif_is_valid_against_the_official_schema_and_keeps_unavailable_engines() -> None:
    schema = _sarif_schema()
    Draft4Validator.check_schema(schema)
    sarif = build_sarif(_export())
    errors = sorted(Draft4Validator(schema).iter_errors(sarif), key=lambda e: list(e.path))
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors[:5]]
    runs = {r["tool"]["driver"]["name"]: r for r in sarif["runs"]}
    assert runs["opengrep"]["invocations"][0]["executionSuccessful"] is False
    assert runs["opengrep"]["results"] == []
    dependency = runs["trivy"]["results"][0]
    assert "region" not in dependency["locations"][0]["physicalLocation"]  # no invented line
    uri = runs["trivy"]["results"][1]["locations"][0]["physicalLocation"]["artifactLocation"]
    assert uri["uri"] == "web/src/config%20file.js" and uri["uriBaseId"] == "SRCROOT"
    assert dependency["partialFingerprints"]["crpFingerprint/v1"] == "1" * 64
