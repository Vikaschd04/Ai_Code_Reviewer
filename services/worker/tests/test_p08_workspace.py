"""Fix workspaces (P08) on the real stack: issues of a real review are fixed in bulk and by hand,
the workspace is re-checked by the real engines on a copy of the upload (Temporal worker and the
lite in-process runner), and every outcome is honest:
- fixed;
- still present;
- suppressed (never fixed);
- new problems.
The upload, its review and the issue lifecycle stay untouched. Synthetic fixtures only."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration

StackFactory = Callable[..., contextlib.AbstractAsyncContextManager[Any]]
INVOICE = "src/main/java/com/example/billing/InvoiceService.java"
CART = "web/src/cart.ts"
APP = "web/src/app.js"


def _digest(root: Path) -> str:
    return hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file())
    ).hexdigest()


def _git_apply(tree: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "apply", "-p1", "ws.diff"],  # noqa: S607
        cwd=tree,
        capture_output=True,
        check=False,
    )


async def _wait_check(stack: Any, check_id: str) -> dict[str, Any]:
    for _ in range(600):
        check = await stack.ok("GET", f"/v1/change-set-checks/{check_id}")
        if check["state"] in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED"}:
            return dict(check)
        await asyncio.sleep(0.3)
    raise AssertionError("workspace check did not finish")


async def _save(stack: Any, ws: dict[str, Any], path: str, text: str) -> dict[str, Any]:
    saved = await stack.ok(
        "PUT",
        f"/v1/change-sets/{ws['id']}/file",
        json={"version": ws["version"], "path": path, "content": text},
    )
    return dict(saved)


async def _fix_and_check(stack: Any, tmp_path: Path) -> None:
    source = prepare_fixture("seeded-mixed", tmp_path / "upload")
    before = _digest(source)
    project = await stack.project("P08 workspace")
    intake = await stack.zip_intake(project, zip_directory(source))
    scan = await stack.scan_and_wait(project, intake["snapshot_id"])
    findings = await stack.findings(scan["id"])
    target = next(f for f in findings if f["rule_id"] == "UseEqualsToCompareStrings")
    others_in_invoice = [
        f for f in findings if f["path"] == INVOICE and f["rule_id"] != "UseEqualsToCompareStrings"
    ]
    assert others_in_invoice
    cart_text = (source / CART).read_text()
    nan_line = cart_text.splitlines().index("    if (item.price === NaN) {") + 1
    nan_findings = [f for f in findings if f["path"] == CART and f["start_line"] == nan_line]
    assert nan_findings, [(f["rule_id"], f["start_line"]) for f in findings if f["path"] == CART]
    issues_before = await stack.ok("GET", f"/v1/projects/{project}/issues?limit=200")

    ws = await stack.ok("POST", f"/v1/projects/{project}/change-sets", json={})
    assert ws["base_scan_id"] == scan["id"]
    fixed = await stack.ok(
        "POST",
        f"/v1/change-sets/{ws['id']}/fixes",
        json={
            "version": ws["version"],
            "engine": "pmd",
            "rule_id": "UseEqualsToCompareStrings",
        },
    )
    assert [a["finding_id"] for a in fixed["applied"]] == [target["id"]]
    ws = fixed["change_set"]
    # A real fix next to a new suppression marker: the vanished finding is "suppressed".
    silenced = cart_text.replace(
        "    if (item.price === NaN) {",
        "    // eslint-disable-next-line no-dupe-keys\n    if (Number.isNaN(item.price)) {",
    )
    saved = await _save(stack, ws, CART, silenced)
    assert saved["flags"] == ["suppression_added"]
    ws = saved["change_set"]
    # A new problem added by hand is reported as new.
    ws = (
        await _save(
            stack, ws, APP, (source / APP).read_text() + 'export const later = eval("1");\n'
        )
    )["change_set"]

    # A configuration edit (a vulnerable dependency, allowed for manual edits and flagged) is
    # checked across the whole copy, so the dependency check reports it as new.
    pom = (
        (source / "pom.xml")
        .read_text()
        .replace(
            "</project>",
            "  <dependencies>\n    <dependency>\n      <groupId>org.apache.logging.log4j</groupId>\n"
            "      <artifactId>log4j-core</artifactId>\n      <version>2.14.1</version>\n"
            "    </dependency>\n  </dependencies>\n</project>",
        )
    )
    saved = await _save(stack, ws, "pom.xml", pom)
    assert saved["flags"] == ["config_change"]
    ws = saved["change_set"]

    started = await stack.ok("POST", f"/v1/change-sets/{ws['id']}/checks")
    check = await _wait_check(stack, started["id"])
    assert check["state"] in {"SUCCEEDED", "PARTIAL"}, check
    assert check["current"] is True and check["content_sha256"] == ws["content_sha256"]
    result = check["result"]
    outcomes = result["outcomes"]
    assert outcomes[target["id"]] == "fixed"
    for finding in nan_findings:
        assert outcomes[finding["id"]] == "suppressed"
    for finding in others_in_invoice:
        expected = "fixed" if finding["start_line"] == target["start_line"] else "still_present"
        assert outcomes[finding["id"]] == expected, finding  # the same line's issues go together
    assert result["counts"]["fixed"] >= 1 and result["counts"]["new"] >= 1
    assert any(item["path"] == APP for item in result["new_items"])
    assert any(
        item["path"] == "pom.xml" and item["engine"] == "trivy" for item in result["new_items"]
    ), result["new_items"]
    assert result["compiled"] is False and "not compiled" in result["not_verified"]

    # The issue queue shows the outcomes; the summary export carries the check.
    queue = await stack.ok("GET", f"/v1/change-sets/{ws['id']}/issues?outcome=fixed")
    assert target["id"] in {i["finding_id"] for i in queue["items"]}
    summary = await stack.ok("GET", f"/v1/change-sets/{ws['id']}/export?format=summary")
    assert summary["check"]["checked"] is True and summary["check"]["counts"]["fixed"] >= 1

    # The upload, its review list and the issue lifecycle are untouched; the copy stays hidden.
    snapshots = await stack.ok("GET", f"/v1/projects/{project}/snapshots")
    assert [s["id"] for s in snapshots["items"]] == [intake["snapshot_id"]]
    scans = await stack.ok("GET", f"/v1/projects/{project}/scans")
    assert [s["id"] for s in scans["items"]] == [scan["id"]]
    overview = await stack.ok("GET", f"/v1/projects/{project}/overview")
    assert overview["latest_scan"]["id"] == scan["id"]
    assert overview["latest_snapshot"]["id"] == intake["snapshot_id"]
    assert (overview["snapshot_count"], overview["scan_count"]) == (1, 1)
    issues_after = await stack.ok("GET", f"/v1/projects/{project}/issues?limit=200")
    assert issues_after == issues_before
    assert _digest(source) == before

    # The changed-files patch applies to a copy of exactly the upload.
    patch = await stack.client.get(f"/v1/change-sets/{ws['id']}/export?format=patch")
    tree = tmp_path / "apply"
    await asyncio.to_thread(shutil.copytree, source, tree)
    (tree / "ws.diff").write_bytes(patch.content)
    applied = await asyncio.to_thread(_git_apply, tree)
    assert applied.returncode == 0, applied.stderr
    assert '"PAID".equals(status)' in (tree / INVOICE).read_text()
    assert "Number.isNaN(item.price)" in (tree / CART).read_text()

    # A second check of unchanged content reuses the same derived copy.
    again = await stack.ok("POST", f"/v1/change-sets/{ws['id']}/checks")
    second = await _wait_check(stack, again["id"])
    assert second["snapshot_id"] == check["snapshot_id"]
    assert second["result"]["outcomes"] == outcomes


async def test_workspace_check_on_the_temporal_worker(
    settings: Settings, stack_factory: StackFactory, tmp_path: Path
) -> None:
    async with stack_factory(settings) as stack:
        await _fix_and_check(stack, tmp_path)


async def test_workspace_check_on_the_lite_profile(settings: Settings, tmp_path: Path) -> None:
    async with lite_stack(settings) as stack:
        await _fix_and_check(stack, tmp_path)
