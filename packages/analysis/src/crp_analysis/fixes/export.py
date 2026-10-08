"""Fix change summary (``crp-fix-export/v1``): what the patch changes, where it applies, what
was checked and what was not. Validated against its own JSON Schema."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

EXPORT_FORMAT = "crp-fix-export/v1"
NOT_EXECUTED = (
    "Not compiled, built or tested: running the project's code needs an isolated runner or an "
    "authorized platform environment, which refactorX did not use."
)


@cache
def fix_export_schema() -> dict[str, Any]:
    text = (
        resources.files("crp_analysis.schemas")
        .joinpath("crp-fix-export-v1.schema.json")
        .read_text(encoding="utf-8")
    )
    schema: dict[str, Any] = json.loads(text)
    return schema


@cache
def change_set_export_schema() -> dict[str, Any]:
    """Schema of a fix workspace summary (P08)."""
    text = (
        resources.files("crp_analysis.schemas")
        .joinpath("crp-change-set-export-v1.schema.json")
        .read_text(encoding="utf-8")
    )
    schema: dict[str, Any] = json.loads(text)
    return schema


def build_fix_export(
    proposal: dict[str, Any],
    *,
    project: dict[str, Any],
    snapshot: dict[str, Any],
    tool_version: str,
    generated_at: str,
) -> dict[str, Any]:
    """``proposal`` is the API representation (``FixProposalResponse`` as JSON)."""
    validation = proposal["latest_validation"]
    risks = [NOT_EXECUTED]
    if proposal["behaviour_note"]:
        risks.insert(0, proposal["behaviour_note"])
    if proposal["edited"]:
        risks.append("The patch was edited by a person after it was prepared.")
    if validation is None or not validation["current"]:
        risks.append("This exact patch has not been validated.")
    return {
        "format": EXPORT_FORMAT,
        "generated_at": generated_at,
        "tool": {"name": "refactorX", "component": "fixes", "version": tool_version},
        "project": project,
        "snapshot": snapshot,
        "finding": proposal["finding"],
        "fix": {
            key: proposal[key]
            for key in (
                "id",
                "kind",
                "recipe_id",
                "title",
                "explanation",
                "behaviour_note",
                "state",
                "path",
                "base_sha256",
                "result_sha256",
                "patch_sha256",
                "changed_lines",
                "edited",
            )
        },
        "applies_to": {
            "snapshot_id": snapshot["id"],
            "path": proposal["path"],
            "base_sha256": proposal["base_sha256"],
            "instructions": "git apply -p1 <patch> (or patch -p1) in a copy of exactly this upload",
        },
        "validation": {
            "state": validation["state"],
            "current": validation["current"],
            "patch_sha256": validation["patch_sha256"],
            "summary": validation["summary"],
            "steps": validation["steps"],
        }
        if validation
        else None,
        "labels": proposal["labels"],
        "known_risks": risks,
        "patch": proposal["patch"],
    }
