"""What refactorX posts back to a pull request or commit: one check and one summary comment (P06).

The text is plain language. Paths and titles come from the repository and the engines, so they are
escaped before being put into Markdown. Only *new* findings are annotated, capped at GitHub's 50
annotations per request. The check fails only when the project set a severity threshold and a new
finding reaches it. Incomplete reviews are ``neutral``, never ``success``.
"""

from __future__ import annotations

import re
from typing import Any

from crp_core.domain.states import CheckFailThreshold

MARKER = "<!-- refactorx:review -->"
CHECK_NAME = "refactorX"
MAX_ANNOTATIONS = 50
_ORDER = ("critical", "high", "medium", "low", "info")
_RANK = {name: index for index, name in enumerate(_ORDER)}
_MD = re.compile(r"([\\`*_{}\[\]()#+!|<>~])")
_LABELS = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "Info",
}


def md(text: object) -> str:
    """Escape Markdown and strip line breaks (repository text is untrusted)."""
    return _MD.sub(r"\\\1", " ".join(str(text).split()))[:300]


def _meets(severity: str, threshold: str) -> bool:
    if threshold == CheckFailThreshold.NEVER.value:
        return False
    return _RANK.get(severity, 9) <= _RANK.get(threshold, -1)


def conclusion(result: dict[str, Any], threshold: str) -> str:
    new = result.get("new_items", [])
    if result.get("state") not in {"SUCCEEDED", "PARTIAL"}:
        return "neutral"
    if any(_meets(str(item.get("severity")), threshold) for item in new):
        return "failure"
    if result.get("state") == "PARTIAL" or result.get("new", 0):
        return "neutral"
    return "success"


def _headline(result: dict[str, Any], subject: str) -> str:
    if result.get("state") not in {"SUCCEEDED", "PARTIAL"}:
        return f"refactorX could not finish reviewing {subject}"
    count = int(result.get("new", 0))
    if count == 0:
        return f"No new problems in {subject}"
    return f"{count} new problem{'s' if count != 1 else ''} in {subject}"


def _subject(info: dict[str, Any]) -> str:
    if info.get("pr_number"):
        return "this pull request"
    return f"commit {str(info.get('head_sha', ''))[:7]}"


def summary_markdown(info: dict[str, Any], result: dict[str, Any], link: str | None) -> str:
    subject = _subject(info)
    lines = [f"**{_headline(result, subject)}**", ""]
    compared = result.get("compared_with")
    if compared:
        lines.append(f"Compared with {md(compared)}.")
    elif info.get("pr_number") is None:
        lines.append("First review of this branch: everything found is listed as new.")
    counts = result.get("by_severity") or {}
    if counts:
        lines += ["", "| Severity | New |", "|---|---|"]
        lines += [f"| {_LABELS[s]} | {counts[s]} |" for s in _ORDER if counts.get(s)]
    lines += [
        "",
        f"Fixed: {result.get('fixed', 0)} · Still present: {result.get('unchanged', 0)} · "
        f"Not rechecked: {result.get('not_rechecked', 0)}",
    ]
    new = result.get("new_items") or []
    if new:
        lines += ["", "New problems:"]
        for item in new[:10]:
            where = md(item.get("path", ""))
            if item.get("line"):
                where += f":{item['line']}"
            label = _LABELS.get(str(item.get("severity", "")), "")
            lines.append(f"- {label}: {md(item.get('title'))} ({where})")
        if len(new) > 10:
            lines.append(f"- … and {len(new) - 10} more")
    incomplete = result.get("incomplete") or []
    if incomplete:
        lines += ["", "Not every check finished: " + ", ".join(md(x) for x in incomplete) + "."]
    if link:
        lines += ["", f"[Open the review in refactorX]({link})"]
    lines += ["", "_Source-level review: nothing was built, run or deployed._"]
    return "\n".join(lines)


def check_run_payload(
    info: dict[str, Any], result: dict[str, Any], threshold: str, link: str | None
) -> dict[str, Any]:
    annotations = []
    for item in (result.get("new_items") or [])[:MAX_ANNOTATIONS]:
        if not item.get("line") or not item.get("path"):
            continue
        severity = str(item.get("severity", ""))
        label = _LABELS.get(severity, severity)
        annotations.append(
            {
                "path": item["path"],
                "start_line": int(item["line"]),
                "end_line": int(item.get("end_line") or item["line"]),
                "annotation_level": "failure" if _meets(severity, threshold) else "warning",
                "title": str(item.get("title", ""))[:255],
                "message": f"{label}: {item.get('message') or item.get('title')}"[:4000],
            }
        )
    payload: dict[str, Any] = {
        "name": CHECK_NAME,
        "head_sha": info["head_sha"],
        "status": "completed",
        "conclusion": conclusion(result, threshold),
        "external_id": str(info["review_id"]),
        "output": {
            "title": _headline(result, _subject(info))[:255],
            "summary": summary_markdown(info, result, link)[:60000],
            "annotations": annotations,
        },
    }
    if link:
        payload["details_url"] = link
    return payload


def comment_body(info: dict[str, Any], result: dict[str, Any], link: str | None) -> str:
    return f"{MARKER}\n{summary_markdown(info, result, link)}"
