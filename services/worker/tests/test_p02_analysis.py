"""Phase 2 on real infrastructure and real engines (PMD, ESLint, Opengrep 1.30.0, Trivy 0.69.3
with an offline DB): correlation, dependency/secret findings, issue lifecycle with strict recheck
states, comparison, exports validated against real schemas, graph APIs, stale-graph protection,
per-file caching with config/rule invalidation, and authorization boundaries."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft4Validator, Draft202012Validator

from crp_analysis.engines.eslint import EslintAdapter
from crp_analysis.engines.opengrep import OpengrepAdapter
from crp_analysis.reports import export_schema
from crp_core.config import Settings
from crp_core.db.models import GraphBuild, Issue, Project, Scan, Snapshot, Source, Workspace
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
StackFactory = Callable[..., contextlib.AbstractAsyncContextManager[Any]]


def _engines(scan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["engine"]: e for e in scan["engines"]}


async def _snapshot(stack: Any, project: str, folder: Path) -> str:
    intake = await stack.zip_intake(project, zip_directory(folder))
    assert intake["state"] == "READY", intake
    return str(intake["snapshot_id"])


async def _issues(stack: Any, project: str, **params: str) -> dict[str, Any]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    page = await stack.ok("GET", f"/v1/projects/{project}/issues?limit=200&{query}")
    assert page["next_cursor"] is None
    return dict(page)


def _sarif_validator() -> Draft4Validator:
    schema = json.loads((REPO / "packages/analysis/tests/data/sarif-schema-2.1.0.json").read_text())
    return Draft4Validator(schema)


async def _foreign_rows(settings: Settings) -> dict[str, uuid.UUID]:
    """Rows in another workspace; the local principal has no grant there."""
    ids = {k: uuid.uuid4() for k in ("ws", "project", "source", "snapshot", "scan", "issue")}
    engine = create_engine_from_settings(settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            session.add(Workspace(id=ids["ws"], slug=f"f-{ids['ws'].hex[:8]}", name="Foreign"))
            await session.flush()
            session.add(
                Project(
                    id=ids["project"], workspace_id=ids["ws"], slug="p", name="P", origin="user"
                )
            )
            await session.flush()
            scope = {"workspace_id": ids["ws"], "project_id": ids["project"]}
            session.add(Source(id=ids["source"], mode="zip_upload", display_name="x", **scope))
            await session.flush()
            session.add(
                Snapshot(
                    id=ids["snapshot"],
                    source_id=ids["source"],
                    capture_status="FROZEN",
                    manifest_sha256="a" * 64,
                    frozen_at=datetime.now(UTC),
                    **scope,
                )
            )
            await session.flush()
            session.add(
                Scan(
                    id=ids["scan"],
                    snapshot_id=ids["snapshot"],
                    mode="baseline",
                    policy_version="v1",
                    idempotency_key="foreign-key-1",
                    state="SUCCEEDED",
                    **scope,
                )
            )
            session.add(
                Issue(
                    id=ids["issue"],
                    fingerprint="b" * 64,
                    engine="pmd",
                    rule_id="R",
                    path="A.java",
                    title="t",
                    severity="low",
                    category="correctness",
                    status="OPEN",
                    recheck_state="VERIFIED_PRESENT",
                    **scope,
                )
            )
            session.add(
                GraphBuild(
                    snapshot_id=ids["snapshot"],
                    state="SUCCEEDED",
                    is_current=True,
                    extractor="x",
                    **scope,
                )
            )
    finally:
        await engine.dispose()
    return ids


async def test_security_engines_correlation_exports_and_authorization(
    settings: Settings, tmp_path: Path, stack_factory: StackFactory
) -> None:
    folder = prepare_fixture("security-mixed", tmp_path / "security")
    async with stack_factory(settings) as stack:
        project = await stack.project("Security")
        snapshot = await _snapshot(stack, project, folder)
        done = await stack.scan_and_wait(project, snapshot)
        engines = _engines(done)
        assert done["state"] == "SUCCEEDED", engines
        for name in ("pmd", "eslint", "opengrep", "trivy", "graph", "structure"):
            assert engines[name]["state"] == "SUCCEEDED", (name, engines[name])
        assert engines["opengrep"]["engine_version"] == "1.30.0"
        assert engines["trivy"]["engine_version"] == "0.69.3"
        assert engines["trivy"]["enabled_rule_count"] is None  # open-ended vulnerability DB
        assert engines["trivy"]["diagnostics"]["cache"]["eligible"] is False
        assert engines["trivy"]["diagnostics"]["db_updated_at"]

        findings = await stack.findings(done["id"])
        by_rule = {(f["engine"], f["rule_id"], f["path"]): f for f in findings}
        log4shell = by_rule[("trivy", "CVE-2021-44228", "pom.xml")]
        assert log4shell["anchor_kind"] == "dependency"
        assert log4shell["category"] == "dependencies" and log4shell["severity"] == "critical"
        assert log4shell["details"]["installed_version"] == "2.14.1"
        detail = await stack.ok("GET", f"/v1/findings/{log4shell['id']}")
        assert "CVE-2021-44228" in detail["rule"]["title"]
        assert "Trivy vulnerability database" in detail["rule"]["severity_rationale"]

        secret = by_rule[("trivy", "secret:github-pat", "web/src/config.js")]
        assert "ghp_" not in secret["message"]
        secret_detail = await stack.ok("GET", f"/v1/findings/{secret['id']}")
        shown = "\n".join(secret_detail["source"]["lines"])
        assert "ghp_" not in shown and secret_detail["source"]["redactions"] >= 1

        eval_findings = [
            f for f in findings if f["path"] == "web/src/server.js" and "eval" in f["rule_id"]
        ]
        assert {f["engine"] for f in eval_findings} == {"eslint", "opengrep"}
        assert len({f["correlation_key"] for f in eval_findings}) == 1
        for f in eval_findings:
            assert [r["engine"] for r in f["also_reported_by"]] == [
                "opengrep" if f["engine"] == "eslint" else "eslint"
            ]
        detail = await stack.ok("GET", f"/v1/findings/{eval_findings[0]['id']}")
        assert len(detail["related"]) == 1

        issues = await _issues(stack, project)
        assert issues["total"] == len(findings)
        assert issues["by_status"] == {"OPEN": len(findings)}
        assert issues["by_recheck"] == {"VERIFIED_PRESENT": len(findings)}
        assert all(f["issue"]["status"] == "OPEN" for f in findings)
        assert done["summary"]["lifecycle"]["applied"] is True

        export = (await stack.client.get(f"/v1/scans/{done['id']}/export?format=json")).json()
        Draft202012Validator(export_schema()).validate(export)
        assert len(export["findings"]) == len(findings)
        response = await stack.client.get(f"/v1/scans/{done['id']}/export?format=sarif")
        assert response.headers["content-type"].startswith("application/sarif+json")
        assert "attachment" in response.headers["content-disposition"]
        sarif = response.json()
        errors = list(_sarif_validator().iter_errors(sarif))
        assert not errors, [e.message for e in errors[:3]]
        assert sum(len(r["results"]) for r in sarif["runs"]) == len(findings)

        foreign = await _foreign_rows(settings)
        for probe in (
            f"/v1/scans/{foreign['scan']}/export",
            f"/v1/scans/{done['id']}/compare?base={foreign['scan']}",
            f"/v1/snapshots/{foreign['snapshot']}/graph",
            f"/v1/snapshots/{foreign['snapshot']}/graph/nodes",
            f"/v1/projects/{foreign['project']}/issues",
            f"/v1/issues/{foreign['issue']}",
        ):
            assert (await stack.client.get(probe)).status_code == 404, probe
        patch = await stack.client.patch(
            f"/v1/issues/{foreign['issue']}", json={"version": 1, "status": "TRIAGED"}
        )
        assert patch.status_code == 404


async def test_graph_build_apis_and_failed_rebuild_hides_stale_links(
    settings: Settings,
    tmp_path: Path,
    stack_factory: StackFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = prepare_fixture("graph-mixed", tmp_path / "graph")
    async with stack_factory(settings) as stack:
        project = await stack.project("Graph")
        snapshot = await _snapshot(stack, project, folder)
        done = await stack.scan_and_wait(project, snapshot)
        graph_run = _engines(done)["graph"]
        assert graph_run["state"] == "PARTIAL"  # shared/src/broken.ts has a syntax error
        assert done["state"] == "PARTIAL"
        base = f"/v1/snapshots/{snapshot}/graph"
        summary = await stack.ok("GET", base)
        assert summary["status"] == "current" and summary["build"]["state"] == "PARTIAL"
        labels = {m["node"]["label"] for m in summary["modules"]}
        assert {"core", "app", "@acme/web", "@acme/shared"} <= labels
        deps = {
            (d["source_key"], d["target_key"], d["relation"]): d
            for d in summary["module_dependencies"]
        }
        assert deps[("module:maven:app", "module:maven:core", "depends_on")]["classification"] == (
            "resolved"
        )
        assert ("module:maven:app", "module:maven:core", "code") in deps
        assert summary["edges_by_classification"]["unresolved"] >= 4
        assert summary["unresolved_reasons"]

        found = await stack.ok("GET", f"{base}/nodes?q=CustomerRepository.java&kind=file")
        assert found["total"] == 1
        node = found["items"][0]
        assert node["path"] == "app/src/main/java/com/acme/app/CustomerRepository.java"
        hood = await stack.ok("GET", f"{base}/nodes/{node['id']}/neighborhood?depth=1")
        classes = {(e["target_ref"], e["classification"]) for e in hood["edges"]}
        assert ("org.slf4j.Logger", "inferred") in classes
        assert ("com.unknown.Missing", "unresolved") in classes
        assert all(e["evidence_path"] and e["evidence_text"] for e in hood["edges"])
        bounded = await stack.ok("GET", f"{base}/nodes/{node['id']}/neighborhood?depth=2&limit=5")
        assert bounded["truncated"] is True and len(bounded["nodes"]) <= 5
        too_deep = await stack.client.get(f"{base}/nodes/{node['id']}/neighborhood?depth=3")
        assert too_deep.status_code == 400  # request validation: depth is capped at 2

        entity = (await stack.ok("GET", f"{base}/nodes?q=Entity&kind=type"))["items"][0]
        impact = await stack.ok("GET", f"{base}/nodes/{entity['id']}/impact?depth=3")
        dependents = {i["node"]["path"]: i for i in impact["dependents"]}
        assert dependents["app/src/main/java/com/acme/app/Customer.java"]["depth"] == 1
        assert impact["unresolved_edges_in_build"] > 0 and impact["caveats"]
        base_view = (await stack.ok("GET", f"{base}/nodes?q=web/src/views/base.ts&kind=file"))[
            "items"
        ][0]
        impact = await stack.ok("GET", f"{base}/nodes/{base_view['id']}/impact?depth=2")
        depth = {i["node"]["path"]: i["depth"] for i in impact["dependents"]}
        assert depth["web/src/app.ts"] == 1 and depth["web/src/views/home.tsx"] == 1
        assert depth["web/src/index.ts"] == 2

        # A failed re-extraction becomes current and hides the earlier links.
        def explode(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated parser crash")

        monkeypatch.setattr("crp_worker.graph_job.extract_facts", explode)
        failed = await stack.scan_and_wait(project, snapshot, cache_mode="refresh")
        assert _engines(failed)["graph"]["state"] == "FAILED"
        assert failed["state"] == "PARTIAL"
        summary = await stack.ok("GET", base)
        assert summary["status"] == "failed" and summary["modules"] == []
        response = await stack.client.get(f"{base}/nodes/{node['id']}/neighborhood")
        assert response.status_code == 409
        assert response.json()["code"] == "graph_build_failed"

        monkeypatch.undo()
        recovered = await stack.scan_and_wait(project, snapshot, cache_mode="refresh")
        assert _engines(recovered)["graph"]["state"] == "PARTIAL"
        assert (await stack.ok("GET", base))["status"] == "current"


def _eslint_without(tmp_path: Path, rule: str) -> EslintAdapter:
    """A copy of the trusted ESLint runner with one rule removed: a real rule-set change."""
    source = REPO / "engines" / "eslint-runner"
    runner = tmp_path / "eslint-runner-changed"
    runner.mkdir()
    shutil.copy(source / "run.mjs", runner / "run.mjs")
    shutil.copy(source / "package.json", runner / "package.json")
    (runner / "node_modules").symlink_to(source / "node_modules")
    config = (source / "trusted.config.mjs").read_text()
    changed = config.replace(f'  "{rule}": "error",\n', "")
    assert changed != config
    (runner / "trusted.config.mjs").write_text(changed)
    return EslintAdapter(runner, node_executable=None, timeout_seconds=120, max_output_bytes=10**7)


async def test_cache_reuse_and_invalidation_by_config_and_rule_changes(
    settings: Settings, tmp_path: Path, stack_factory: StackFactory
) -> None:
    folder = prepare_fixture("seeded-mixed", tmp_path / "seeded")
    async with stack_factory(settings) as stack:
        project = await stack.project("Cache")
        snapshot = await _snapshot(stack, project, folder)
        first = await stack.scan_and_wait(project, snapshot)
        second = await stack.scan_and_wait(project, snapshot)
        refreshed = await stack.scan_and_wait(project, snapshot, cache_mode="refresh")
        one, two, three = _engines(first), _engines(second), _engines(refreshed)
        for name in ("pmd", "eslint", "opengrep", "graph"):
            eligible = one[name]["files_eligible"]
            assert (one[name]["cache_hits"], one[name]["cache_misses"]) == (0, eligible), name
            analyzed = one[name]["files_succeeded"]
            assert two[name]["cache_hits"] == analyzed > 0, name  # failed parses never cached
            assert two[name]["cache_misses"] == eligible - analyzed, name
            assert three[name]["cache_hits"] == 0, name
            assert two[name]["state"] == one[name]["state"], name
            assert two[name]["ruleset_id"] == one[name]["ruleset_id"] is not None, name
            assert two[name]["ruleset_sha256"] == one[name]["ruleset_sha256"], name
        assert two["trivy"]["cache_hits"] == 0  # whole-tree engine: never cached per file
        first_fps = sorted(f["fingerprint"] for f in await stack.findings(first["id"]))
        second_findings = await stack.findings(second["id"])
        assert sorted(f["fingerprint"] for f in second_findings) == first_fps
        coverage = (await stack.ok("GET", f"/v1/scans/{second['id']}/coverage?engine=pmd"))["items"]
        assert any(c["cached"] and "cached" in c["reason"] for c in coverage)
        assert any(not c["cached"] and c["outcome"] == "FAILED" for c in coverage)
        no_eval = [f for f in second_findings if f["rule_id"] == "no-eval"]
        assert no_eval and no_eval[0]["issue"]["status"] == "OPEN"

    # Configuration-only change (Opengrep target-size limit): Opengrep misses, others hit.
    changed_opengrep = OpengrepAdapter(
        REPO / ".local" / "engines" / "opengrep-1.30.0",
        timeout_seconds=120,
        max_output_bytes=10**7,
        max_target_bytes=settings.intake_max_text_file_bytes - 1,
    )
    async with stack_factory(settings, adapters={"opengrep": changed_opengrep}) as stack:
        config_scan = _engines(await stack.scan_and_wait(project, snapshot))
        assert config_scan["opengrep"]["cache_hits"] == 0
        assert config_scan["pmd"]["cache_hits"] == two["pmd"]["cache_hits"]

    # Rule-set change (no-eval removed): ESLint misses and no-eval issues become RULE_OBSOLETE.
    async with stack_factory(
        settings, adapters={"eslint": _eslint_without(tmp_path, "no-eval")}
    ) as stack:
        rules_scan = await stack.scan_and_wait(project, snapshot)
        eslint = _engines(rules_scan)["eslint"]
        assert eslint["cache_hits"] == 0 and eslint["cache_misses"] == eslint["files_eligible"]
        assert eslint["ruleset_sha256"] != two["eslint"]["ruleset_sha256"]
        obsolete = await _issues(stack, project, recheck_state="RULE_OBSOLETE")
        assert {i["rule_id"] for i in obsolete["items"]} == {"no-eval"}
        assert all(i["status"] == "OPEN" for i in obsolete["items"])  # never auto-resolved
        # Other ESLint issues: same engine version but different rule-set hash -> UNKNOWN when
        # absent; still reported ones stay VERIFIED_PRESENT (fingerprints unchanged).
        present = await _issues(stack, project, recheck_state="VERIFIED_PRESENT")
        assert any(i["engine"] == "eslint" for i in present["items"])


async def test_issue_lifecycle_comparison_and_triage_across_snapshots(
    settings: Settings, tmp_path: Path, stack_factory: StackFactory
) -> None:
    original = prepare_fixture("seeded-mixed", tmp_path / "a")
    changed = prepare_fixture("seeded-mixed", tmp_path / "b")
    app = changed / "web" / "src" / "app.js"
    app.write_text(app.read_text().replace("  debugger;\n", ""))  # a real fix
    (changed / "src/main/java/com/example/billing/CryptoUtil.java").unlink()  # deletion
    cart = changed / "web" / "src" / "cart.ts"
    cart.write_text(cart.read_text() + "\nexport function broken( {\n")  # now unparseable

    async with stack_factory(settings) as stack:
        project = await stack.project("Lifecycle")
        snap_a = await _snapshot(stack, project, original)
        scan_a = await stack.scan_and_wait(project, snap_a)
        issues_a = {i["id"]: i for i in (await _issues(stack, project))["items"]}
        debugger = next(i for i in issues_a.values() if i["rule_id"] == "no-debugger")
        crypto = [i for i in issues_a.values() if i["path"].endswith("CryptoUtil.java")]
        cart_issues = [
            i
            for i in issues_a.values()
            if i["path"] == "web/src/cart.ts" and i["engine"] == "eslint"
        ]
        assert crypto and cart_issues

        # Accept one risk with a short expiry: it must lapse back to OPEN at a later scan.
        accepted = next(
            i
            for i in issues_a.values()
            if i["path"] == "web/src/app.js" and i["rule_id"] == "eqeqeq"
        )
        bad = await stack.client.patch(
            f"/v1/issues/{accepted['id']}",
            json={"version": accepted["version"], "status": "ACCEPTED_RISK", "reason": "legacy"},
        )
        assert bad.status_code == 422 and bad.json()["code"] == "expiry_required"
        soon = (datetime.now(UTC) + timedelta(seconds=4)).isoformat()
        ok = await stack.ok(
            "PATCH",
            f"/v1/issues/{accepted['id']}",
            json={
                "version": accepted["version"],
                "status": "ACCEPTED_RISK",
                "reason": "legacy module, replacement scheduled",
                "expires_at": soon,
                "owner": "billing-team",
            },
        )
        assert ok["issue"]["status"] == "ACCEPTED_RISK" and ok["issue"]["owner"] == "billing-team"
        assert ok["issue"]["version"] == accepted["version"] + 1
        assert ok["events"][0]["kind"] == "triaged" and ok["events"][0]["actor_kind"] == "user"
        stale = await stack.client.patch(
            f"/v1/issues/{accepted['id']}", json={"version": accepted["version"], "owner": "x"}
        )
        assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
        fp = next(i for i in issues_a.values() if i["id"] not in {accepted["id"], debugger["id"]})
        no_reason = await stack.client.patch(
            f"/v1/issues/{fp['id']}", json={"version": fp["version"], "status": "FALSE_POSITIVE"}
        )
        assert no_reason.status_code == 422

        snap_b = await _snapshot(stack, project, changed)
        scan_b = await stack.scan_and_wait(project, snap_b)
        assert scan_b["summary"]["lifecycle"]["applied"] is True
        after_b = {i["id"]: i for i in (await _issues(stack, project))["items"]}
        assert after_b[debugger["id"]]["recheck_state"] == "VERIFIED_ABSENT"
        assert after_b[debugger["id"]]["status"] == "RESOLVED"
        assert {after_b[i["id"]]["recheck_state"] for i in crypto} == {"UNKNOWN"}
        assert {after_b[i["id"]]["status"] for i in crypto} == {"OPEN"}  # never "fixed"
        assert {after_b[i["id"]]["recheck_state"] for i in cart_issues} == {"NOT_RECHECKED"}
        resolved = await stack.client.patch(
            f"/v1/issues/{debugger['id']}",
            json={"version": after_b[debugger["id"]]["version"], "status": "TRIAGED"},
        )
        assert resolved.status_code == 409 and resolved.json()["code"] == "issue_resolved"
        detail = await stack.ok("GET", f"/v1/issues/{debugger['id']}")
        assert [e["kind"] for e in detail["events"]][:2] == ["resolved", "recheck_changed"]

        comparison = await stack.ok("GET", f"/v1/scans/{scan_b['id']}/compare?base={scan_a['id']}")
        verified = {(i["rule_id"], i["path"]) for i in comparison["verified_absent"]["items"]}
        assert ("no-debugger", "web/src/app.js") in verified
        unknown_paths = {i["path"] for i in comparison["unknown"]["items"]}
        assert "src/main/java/com/example/billing/CryptoUtil.java" in unknown_paths
        assert {i["path"] for i in comparison["not_rechecked"]["items"]} >= {"web/src/cart.ts"}
        assert comparison["unchanged"]["count"] > 0 and comparison["new"]["count"] == 0
        applicable = [
            e
            for e in comparison["engines"]
            if e["engine"] != "trivy" and e["base_state"] != "NOT_APPLICABLE"
        ]
        assert applicable and all(e["compatible"] for e in applicable)
        idle = {e["engine"] for e in comparison["engines"] if e["base_state"] == "NOT_APPLICABLE"}
        # Platform checks and architecture rules (none set): nothing to compare.
        assert idle == {"pmd-apex", "frameworks", "architecture"}

        # Graph nodes are served only through their own snapshot's current build.
        node_a = (await stack.ok("GET", f"/v1/snapshots/{snap_a}/graph/nodes?limit=1"))["items"][0]
        cross = await stack.client.get(f"/v1/snapshots/{snap_b}/graph/nodes/{node_a['id']}/impact")
        assert cross.status_code == 404

        # Re-scanning the older snapshot does not move issue state backwards.
        await asyncio.sleep(4)  # let the accepted-risk exception expire
        older = await stack.scan_and_wait(project, snap_a)
        assert older["summary"]["lifecycle"]["applied"] is False
        assert (await stack.ok("GET", f"/v1/issues/{debugger['id']}"))["issue"]["status"] == (
            "RESOLVED"
        )

        # The defect comes back in a newer snapshot: the issue reopens; the exception lapsed.
        snap_c = await _snapshot(stack, project, original)
        scan_c = await stack.scan_and_wait(project, snap_c)
        assert scan_c["summary"]["lifecycle"]["applied"] is True
        reopened = await stack.ok("GET", f"/v1/issues/{debugger['id']}")
        assert reopened["issue"]["status"] == "OPEN"
        assert reopened["issue"]["recheck_state"] == "VERIFIED_PRESENT"
        assert reopened["events"][0]["kind"] in {"reopened", "recheck_changed"}
        assert "reopened" in {e["kind"] for e in reopened["events"]}
        lapsed = await stack.ok("GET", f"/v1/issues/{accepted['id']}")
        assert lapsed["issue"]["status"] == "OPEN"
        assert "exception_expired" in {e["kind"] for e in lapsed["events"]}
        assert {
            (await stack.ok("GET", f"/v1/issues/{i['id']}"))["issue"]["recheck_state"]
            for i in crypto
        } == {"VERIFIED_PRESENT"}
