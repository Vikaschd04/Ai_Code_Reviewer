"""What a recipe needs to know beyond its own finding (ADR 0027): a dependency upgrade must fix
every reported vulnerability of the same library, so each Trivy finding carries the fixed
versions of its siblings (same file, library and installed version in the same review)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from crp_core.db.models import Finding

SiblingKey = tuple[uuid.UUID, str, str]


async def dependency_fixes(session: Any, scan_id: uuid.UUID) -> dict[SiblingKey, list[str]]:
    """(file, package, installed version) -> the fixed versions of each vulnerability."""
    rows = await session.execute(
        select(Finding.file_entry_id, Finding.details).where(
            Finding.scan_id == scan_id, Finding.engine == "trivy"
        )
    )
    found: dict[SiblingKey, list[str]] = {}
    for entry_id, details in rows.all():
        if not isinstance(details, dict) or entry_id is None:
            continue
        package, installed = details.get("package"), details.get("installed_version")
        fixed = details.get("fixed_version")
        if isinstance(package, str) and isinstance(installed, str) and isinstance(fixed, str):
            found.setdefault((entry_id, package, installed), []).append(fixed)
    return found


def details_with_siblings(
    finding: Finding, siblings: dict[SiblingKey, list[str]]
) -> dict[str, object] | None:
    details = finding.details
    if finding.engine != "trivy" or not isinstance(details, dict) or finding.file_entry_id is None:
        return details
    key = (
        finding.file_entry_id,
        str(details.get("package")),
        str(details.get("installed_version")),
    )
    fixed = siblings.get(key)
    return {**details, "fixed_versions": fixed} if fixed else details
