"""Duplicate code (ADD-A3) on the real stack: PMD's CPD finds the copied pricing routine in two
Java services and the copied totals function in two TypeScript modules; each copy is an issue
of the "Logic is not copy-pasted" checkpoint. Removing one copy in a fix workspace makes the
re-check report both copies of that block fixed (the check sees the whole upload)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration
ORDER = "src/main/java/shop/OrderService.java"
DELEGATE = """package shop;

import java.math.BigDecimal;
import java.util.List;

public class OrderService {
    private final CartService cart = new CartService();

    public BigDecimal total(List<BigDecimal> amounts, int quantity, String coupon) {
        return cart.price(amounts, quantity, coupon);
    }
}
"""


async def _wait_check(stack: Any, check_id: str) -> dict[str, Any]:
    for _ in range(900):
        check = await stack.ok("GET", f"/v1/change-set-checks/{check_id}")
        if check["state"] in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED"}:
            return dict(check)
        await asyncio.sleep(0.3)
    raise AssertionError("workspace check did not finish")


async def test_copied_code_is_found_tracked_and_confirmed_removed(
    settings: Settings, tmp_path: Path
) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("Duplicates")
        source = prepare_fixture("duplicates-mixed", tmp_path / "duplicates")
        intake = await stack.zip_intake(project, zip_directory(source))
        scan = await stack.scan_and_wait(project, intake["snapshot_id"])
        cpd = {e["engine"]: e for e in scan["engines"]}["cpd"]
        assert cpd["state"] == "SUCCEEDED", cpd
        assert cpd["diagnostics"]["duplications"] == 2
        assert cpd["diagnostics"]["languages"] == ["java", "typescript"]

        findings = [f for f in await stack.findings(scan["id"]) if f["engine"] == "cpd"]
        assert sorted(f["path"] for f in findings) == [
            "src/main/java/shop/CartService.java",
            ORDER,
            "web/src/cart.ts",
            "web/src/checkout.ts",
        ]  # Catalog.java is unique
        java = next(f for f in findings if f["path"] == ORDER)
        assert java["title"].startswith("Duplicated code (") and java["severity"] == "low"
        assert "also in src/main/java/shop/CartService.java" in java["message"]
        assert java["details"]["copies"] == 2 and java["start_line"] is not None

        data = await stack.ok("GET", f"/v1/projects/{project}/insights")
        checkpoint = next(c for c in data["checkpoints"] if c["id"] == "architecture.duplication")
        assert checkpoint["status"] == "attention" and checkpoint["issue_count"] == 4
        assert checkpoint["fixable_issue_ids"] == []  # extracting shared code is done by hand

        # Remove one copy by delegating to the other; the re-check sees the whole upload.
        ws = await stack.ok("POST", f"/v1/projects/{project}/change-sets", json={})
        saved = await stack.ok(
            "PUT",
            f"/v1/change-sets/{ws['id']}/file",
            json={
                "version": ws["version"],
                "path": ORDER,
                "content": DELEGATE,
                "finding_ids": [java["id"]],
            },
        )
        assert saved["change_set"]["version"] > ws["version"]
        started = await stack.ok("POST", f"/v1/change-sets/{ws['id']}/checks")
        check = await _wait_check(stack, started["id"])
        assert check["state"] in {"SUCCEEDED", "PARTIAL"}, check
        outcomes = check["result"]["outcomes"]
        by_path = {f["path"]: outcomes[f["id"]] for f in findings}
        assert by_path["src/main/java/shop/CartService.java"] == "fixed"  # its copy is gone
        assert by_path[ORDER] == "fixed"
        assert by_path["web/src/cart.ts"] == "still_present"
        assert [i for i in check["result"]["new_items"] if i["engine"] == "cpd"] == []
