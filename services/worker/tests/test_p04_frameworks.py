"""Framework packs (P04) on the real stack: SAP Commerce and Salesforce fixtures are uploaded,
reviewed by the real engines (PMD with the Apex rules, Opengrep, framework configuration checks)
and mapped into the snapshot graph with pack reports. Synthetic fixtures only; no SAP build or
Salesforce org is involved."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

pytestmark = pytest.mark.integration

StackFactory = Callable[..., contextlib.AbstractAsyncContextManager[Any]]


async def _review(stack: Any, fixture: str, tmp_path: Path) -> tuple[dict[str, Any], str]:
    project = await stack.project(f"P04 {fixture}")
    intake = await stack.zip_intake(project, zip_directory(prepare_fixture(fixture, tmp_path)))
    assert intake["state"] == "READY", intake
    scan = await stack.scan_and_wait(project, intake["snapshot_id"])
    return scan, intake["snapshot_id"]


def _engines(scan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["engine"]: e for e in scan["engines"]}


async def test_sap_commerce_upload_is_mapped_and_checked(
    settings: Settings, stack_factory: StackFactory, tmp_path: Path
) -> None:
    async with stack_factory(settings) as stack:
        scan, snapshot = await _review(stack, "sap-commerce-mixed", tmp_path / "sap")
        engines = _engines(scan)
        assert engines["pmd-apex"]["state"] == "NOT_APPLICABLE"  # no Apex in this upload
        assert engines["frameworks"]["state"] == "PARTIAL"  # one malformed extensioninfo.xml
        assert engines["frameworks"]["files_failed"] == 1
        findings = await stack.findings(scan["id"])
        rules = {f["rule_id"] for f in findings}
        assert {
            "crp.sap.flexiblesearch.string-concat",
            "crp.sap.model.save-in-loop",
            "crp.sap.interceptor.persisting-side-effect",
            "crp.sap.cronjob.missing-abort-check",
            "crp.sap.jalo.deprecated-api",
            "crp.sap.extension.dependency-cycle",
        } <= rules
        cycle = next(f for f in findings if f["rule_id"] == "crp.sap.extension.dependency-cycle")
        assert cycle["engine"] == "frameworks" and cycle["severity"] == "high"
        assert cycle["path"].endswith("extensioninfo.xml") and cycle["start_line"] == 4

        summary = await stack.ok("GET", f"/v1/snapshots/{snapshot}/graph")
        [pack] = summary["frameworks"]
        assert (pack["id"], pack["version"], pack["version_status"]) == (
            "sap-commerce",
            "2211.28",
            "supported",
        )
        assert pack["relations"]["injects"] >= 8 and pack["components"]["spring_bean"] >= 10
        assert summary["nodes_by_kind"]["component"] > 0
        assert summary["edges_by_relation"]["intercepts"] == 2
        modules = {m["node"]["label"] for m in summary["modules"]}
        assert {"shopcore", "shopfacades", "shopocc"} <= modules
        config_links = [d for d in summary["module_dependencies"] if d["relation"] == "config"]
        assert config_links  # e.g. facade beans injecting core services across extensions
        beans = await stack.ok(
            "GET", f"/v1/snapshots/{snapshot}/graph/nodes?kind=component&q=loyaltyFacade"
        )
        assert beans["items"] and beans["items"][0]["attributes"]["framework"] == "sap"


async def test_salesforce_upload_is_mapped_and_checked(
    settings: Settings, stack_factory: StackFactory, tmp_path: Path
) -> None:
    async with stack_factory(settings) as stack:
        scan, snapshot = await _review(stack, "salesforce-mixed", tmp_path / "sf")
        engines = _engines(scan)
        assert engines["pmd-apex"]["state"] == "SUCCEEDED", engines["pmd-apex"]
        assert engines["pmd-apex"]["engine_version"] == "7.27.0"
        assert engines["pmd"]["state"] == "NOT_APPLICABLE"  # Apex never goes to the Java rules
        assert engines["frameworks"]["state"] == "PARTIAL"  # the DTD-bearing object is refused
        findings = await stack.findings(scan["id"])
        apex = {f["rule_id"] for f in findings if f["engine"] == "pmd-apex"}
        assert {
            "OperationWithLimitsInLoop",
            "ApexCRUDViolation",
            "ApexSOQLInjection",
            "ApexSharingViolations",
            "ApexUnitTestShouldNotUseSeeAllDataTrue",
        } <= apex
        assert not [f for f in findings if f["path"].endswith("SafeAccountService.cls")]
        retired = [f for f in findings if f["rule_id"] == "crp.sf.metadata.retired-api-version"]
        assert [f["path"].rsplit("/", 1)[-1] for f in retired] == ["LegacyIntegration.cls-meta.xml"]

        summary = await stack.ok("GET", f"/v1/snapshots/{snapshot}/graph")
        [pack] = summary["frameworks"]
        assert (pack["id"], pack["version"], pack["version_status"]) == (
            "salesforce",
            "62.0",
            "supported",
        )
        states = {c["id"]: c["state"] for c in pack["capabilities"]}
        assert states["objects"] == "partial" and states["org_validation"] == "unavailable"
        assert summary["edges_by_relation"]["calls_apex"] == 2
        assert {m["node"]["label"] for m in summary["modules"]} >= {"ShopApp"}
