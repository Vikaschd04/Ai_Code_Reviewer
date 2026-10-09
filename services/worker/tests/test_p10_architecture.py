"""Architecture metrics (P10 slice 1) on the real stack: a synthetic project is uploaded and
reviewed (real graph extraction and resolution), and the metrics API must return the values
computed by hand in packages/analysis/tests/test_architecture_metrics.py."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest

from crp_core.config import Settings

from .conftest import lite_stack

pytestmark = pytest.mark.integration

J = "src/main/java"
PROJECT = {
    f"{J}/app/A1.java": "package app;\n\nimport svc.S1;\n\npublic class A1 { S1 s; }\n",
    f"{J}/app/A2.java": (
        "package app;\n\nimport svc.S1;\nimport dom.D1;\n\npublic class A2 { S1 s; D1 d; }\n"
    ),
    f"{J}/svc/S1.java": (
        "package svc;\n\nimport dom.D1;\nimport dom.D2;\n\n"
        "public class S1 implements D2 { D1 d; }\n"
    ),
    f"{J}/svc/S2.java": "package svc;\n\npublic abstract class S2 { abstract void run(); }\n",
    f"{J}/dom/D1.java": "package dom;\n\npublic class D1 { }\n",
    f"{J}/dom/D2.java": "package dom;\n\npublic interface D2 { }\n",
    f"{J}/x/X.java": "package x;\n\nimport y.Y;\n\npublic class X { Y y; }\n",
    f"{J}/y/Y.java": "package y;\n\nimport x.X;\n\npublic class Y { X x; }\n",
    "src/test/java/app/A1Test.java": "package app;\n\npublic class A1Test { A1 a; }\n",
    "web/a/ui.ts": 'import { api } from "../b/api";\nexport const ui = api;\n',
    "web/b/api.ts": "export const api = 1;\n",
}


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in PROJECT.items():
            archive.writestr(path, text)
    return buffer.getvalue()


async def test_architecture_metrics_from_a_real_review(settings: Settings) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("P10 architecture")
        intake = await stack.zip_intake(project, _zip())
        assert intake["state"] == "READY"
        snapshot = intake["snapshot_id"]
        scan = await stack.scan_and_wait(project, snapshot)
        assert scan["state"] in {"SUCCEEDED", "PARTIAL"}
        data: dict[str, Any] = await stack.ok("GET", f"/v1/snapshots/{snapshot}/architecture")
        assert data["algorithm"] == "crp-architecture-metrics-v1"
        assert "crp-graph-extract-v3" in data["extractor"] and data["notes"]
        by_key = {c["key"]: c for c in data["components"]}
        rows = {
            key: (
                by_key[key]["afferent"],
                by_key[key]["efferent"],
                by_key[key]["instability"],
                by_key[key]["abstractness"],
                by_key[key]["distance"],
            )
            for key in ("app", "svc", "dom")
        }
        assert rows == {
            "app": (0, 2, 1.0, 0.0, 0.0),
            "svc": (2, 1, 0.333, 0.5, 0.167),
            "dom": (2, 0, 0.0, 0.5, 0.5),
        }
        assert by_key["web/a"]["kind"] == "folder" and by_key["web/a"]["efferent"] == 1
        assert by_key["web/b"]["afferent"] == 1
        # The test file is left out of the model (and counted).
        assert data["summary"]["test_files"] == 1 and by_key["app"]["files"] == 2
        # The package cycle x <-> y, with one dependency to cut.
        (cycle,) = data["cycles"]
        assert cycle["components"] == ["x", "y"] and len(cycle["cut"]) == 1 and cycle["exact"]
        assert by_key["x"]["in_cycle"] and by_key["y"]["in_cycle"]
        edges = {(e["source"], e["target"]): e["weight"] for e in data["edges"]}
        assert edges[("app", "svc")] == 2 and edges[("svc", "dom")] == 2
        summary = data["summary"]
        assert summary["cycles"] == 1 and summary["components_in_cycles"] == 2
        # Other workspaces see nothing.
        other = await stack.client.get(
            f"/v1/snapshots/{snapshot}/architecture", headers={"Authorization": "Bearer nope"}
        )
        assert other.status_code == 401


# -- intended architecture (P10 slice 2; ADR 0019) ----------------------------------------------

A = "src/main/java/com/acme"
RULED = {
    f"{A}/web/Controller.java": (
        "package com.acme.web;\n\nimport com.acme.service.OrderService;\n\n"
        "public class Controller { OrderService s; }\n"
    ),
    f"{A}/service/OrderService.java": (
        "package com.acme.service;\n\nimport com.acme.domain.Order;\nimport com.acme.web.Controller;"
        "\n\npublic class OrderService { Order o; Controller c; }\n"
    ),
    f"{A}/domain/Order.java": (
        "package com.acme.domain;\n\nimport com.acme.legacy.OldOrder;\n\n"
        "public class Order { OldOrder old; }\n"
    ),
    f"{A}/domain/Status.java": (
        "package com.acme.domain;\n\nimport com.acme.service.OrderService;\n\n"
        "public class Status { OrderService s; }\n"
    ),
    f"{A}/legacy/OldOrder.java": "package com.acme.legacy;\n\npublic class OldOrder { }\n",
    "src/test/java/com/acme/domain/OrderTest.java": (
        "package com.acme.domain;\n\nimport com.acme.legacy.OldOrder;\n\n"
        "public class OrderTest { OldOrder o; }\n"
    ),
}
# The service no longer reaches up into the web layer.
FIXED = {
    **RULED,
    f"{A}/service/OrderService.java": (
        "package com.acme.service;\n\nimport com.acme.domain.Order;\n\n"
        "public class OrderService { Order o; }\n"
    ),
}
RULES = """\
layers:
  - name: web
    match: [com.acme.web.**]
  - name: service
    match: [com.acme.service.**]
  - name: domain
    match: [com.acme.domain.**]
forbid:
  - key: domain-no-legacy
    from: domain
    to: com.acme.legacy.**
    reason: Legacy is being removed.
    severity: high
allow:
  - from: domain
    to: service
    reason: Status lookups during the migration.
    until: 2099-12-31
  - from: service
    to: web
    reason: Old view helper.
    until: 2020-01-01
"""
FORBID = "arch.forbid.domain-no-legacy"


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return buffer.getvalue()


async def _architecture_issues(stack: Any, project: str) -> dict[str, dict[str, Any]]:
    page = await stack.ok("GET", f"/v1/projects/{project}/issues?engine=architecture&limit=200")
    assert sum(page["by_status"].values()) == len(page["items"])  # counts follow the check
    return {issue["rule_id"]: issue for issue in page["items"]}


async def _save(stack: Any, project: str, text: str, base: int) -> dict[str, Any]:
    return dict(
        await stack.ok(
            "PUT",
            f"/v1/projects/{project}/architecture-rules",
            json={"yaml": text, "note": f"version after {base}", "base_version": base},
        )
    )


async def test_architecture_rules_on_real_reviews(settings: Settings) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("P10 rules")
        first = await stack.zip_intake(project, _zip_of(RULED))
        snapshot = first["snapshot_id"]

        # Without rules the check does not apply, and says why.
        scan = await stack.scan_and_wait(project, snapshot)
        engines = {e["engine"]: e for e in scan["engines"]}
        assert engines["architecture"]["state"] == "NOT_APPLICABLE"
        assert engines["architecture"]["error_message"] == (
            "No architecture rules are set for this project."
        )

        saved = await _save(stack, project, RULES, 0)
        assert saved["version"] == 1

        # A read-only check of the saved rules on this upload.
        check = await stack.ok(
            "POST", f"/v1/snapshots/{snapshot}/architecture-rules/check", json={}
        )
        assert check["rules_version"] == 1 and check["violation_count"] == 2
        assert check["by_rule"] == {FORBID: 1, "arch.layers": 1}
        assert check["allowed_by_exception"] == 1  # Status -> OrderService, until 2099
        assert check["expired_exceptions"] == ["service → web (expired 2020-01-01)"]
        assert check["unassigned"] == ["com.acme.legacy"]
        assert {layer["name"]: layer["parts"] for layer in check["layers"]} == {
            "web": ["com.acme.web"],
            "service": ["com.acme.service"],
            "domain": ["com.acme.domain"],
        }

        # Every review now checks the rules; breaches are findings at the import.
        scan = await stack.scan_and_wait(project, snapshot)
        engines = {e["engine"]: e for e in scan["engines"]}
        run = engines["architecture"]
        assert run["state"] == "SUCCEEDED" and run["findings_count"] == 2
        assert run["files_eligible"] == 5  # the test file is left out
        assert run["diagnostics"]["rules_version"] == 1
        findings = {
            (f["rule_id"], f["path"], f["start_line"], f["severity"])
            for f in await stack.findings(scan["id"])
            if f["engine"] == "architecture"
        }
        assert findings == {
            (FORBID, f"{A}/domain/Order.java", 3, "high"),
            ("arch.layers", f"{A}/service/OrderService.java", 4, "medium"),
        }
        issues = await _architecture_issues(stack, project)
        assert {k: (v["status"], v["recheck_state"]) for k, v in issues.items()} == {
            FORBID: ("OPEN", "VERIFIED_PRESENT"),
            "arch.layers": ("OPEN", "VERIFIED_PRESENT"),
        }
        assert issues["arch.layers"]["title"] == "Layer service must not use web"

        # The code is fixed: the layering breach is verified gone; the forbidden one stays.
        second = await stack.zip_intake(project, _zip_of(FIXED))
        fixed_snapshot = second["snapshot_id"]
        await stack.scan_and_wait(project, fixed_snapshot)
        issues = await _architecture_issues(stack, project)
        assert (issues["arch.layers"]["status"], issues["arch.layers"]["recheck_state"]) == (
            "RESOLVED",
            "VERIFIED_ABSENT",
        )
        assert issues[FORBID]["recheck_state"] == "VERIFIED_PRESENT"

        # The rule changes so that it no longer matches: that is not a fix.
        await _save(stack, project, RULES.replace("com.acme.legacy.**", "com.acme.old.**"), 1)
        scan = await stack.scan_and_wait(project, fixed_snapshot)
        run = {e["engine"]: e for e in scan["engines"]}["architecture"]
        assert run["findings_count"] == 0
        assert run["diagnostics"]["notes"] == [
            "forbid \"domain-no-legacy\": to 'com.acme.old.**' matches no part"
        ]
        issues = await _architecture_issues(stack, project)
        assert issues[FORBID]["status"] == "OPEN"
        assert issues[FORBID]["recheck_state"] == "UNKNOWN"
        assert issues[FORBID]["recheck_reason"] == (
            f"rule {FORBID} changed since the earlier observation"
        )
        assert issues["arch.layers"]["status"] == "RESOLVED"  # its own rule did not change

        # The rule is removed: obsolete, still not fixed.
        layers_only = RULES.split("forbid:")[0] + "allow:" + RULES.split("allow:")[1]
        await _save(stack, project, layers_only, 2)
        await stack.scan_and_wait(project, fixed_snapshot)
        issues = await _architecture_issues(stack, project)
        assert (issues[FORBID]["status"], issues[FORBID]["recheck_state"]) == (
            "OPEN",
            "RULE_OBSOLETE",
        )


async def test_architecture_rules_run_after_the_graph_on_temporal(
    settings: Settings, stack_factory: Any
) -> None:
    """On Temporal the other engines run in parallel; the rules wait for the dependency map."""
    async with stack_factory(settings) as stack:
        project = await stack.project("P10 rules on Temporal")
        intake = await stack.zip_intake(project, _zip_of(RULED))
        await _save(stack, project, RULES, 0)
        scan = await stack.scan_and_wait(project, intake["snapshot_id"])
        run = {e["engine"]: e for e in scan["engines"]}["architecture"]
        assert run["state"] == "SUCCEEDED", run
        assert run["findings_count"] == 2
        graph = {e["engine"]: e for e in scan["engines"]}["graph"]
        assert graph["finished_at"] <= run["started_at"]


# -- architecture smells (P10 slice 3; ADR 0020) ------------------------------------------------

S = "src/main/java/com/acme"
SMELLY = {
    f"{S}/hub/Hub.java": "package com.acme.hub;\n\n"
    + "".join(f"import com.acme.p{i}.P{i};\n" for i in range(1, 5))
    + "\npublic class Hub {\n"
    + "".join(f"    P{i} p{i};\n" for i in range(1, 5))
    + "}\n",
    **{
        f"{S}/p{i}/P{i}.java": f"package com.acme.p{i};\n\npublic class P{i} {{ }}\n"
        for i in range(1, 5)
    },
    **{
        f"{S}/u{i}/U{i}.java": f"package com.acme.u{i};\n\nimport com.acme.hub.Hub;\n\n"
        f"public class U{i} {{ Hub hub; }}\n"
        for i in range(1, 5)
    },
    f"{S}/x/X.java": "package com.acme.x;\n\nimport com.acme.y.Y;\n\npublic class X { Y y; }\n",
    f"{S}/y/Y.java": "package com.acme.y;\n\nimport com.acme.x.X;\n\npublic class Y { X x; }\n",
}
# The cycle is broken, and a new first file of the hub package moves the hub's anchor.
CLEANED = {
    **SMELLY,
    f"{S}/y/Y.java": "package com.acme.y;\n\npublic class Y { }\n",
    f"{S}/hub/AHelper.java": "package com.acme.hub;\n\npublic class AHelper { }\n",
}


async def test_architecture_smells_on_real_reviews(settings: Settings) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("P10 smells")
        first = await stack.zip_intake(project, _zip_of(SMELLY))
        scan = await stack.scan_and_wait(project, first["snapshot_id"])
        run = {e["engine"]: e for e in scan["engines"]}["smells"]
        assert run["state"] == "SUCCEEDED", run
        assert run["diagnostics"]["parts_affected"] == {
            "crp.arch.cycle": 2,
            "crp.arch.unstable-dependency": 0,
            "crp.arch.hub": 1,
        }
        findings = {
            (f["rule_id"], f["path"], f["start_line"])
            for f in await stack.findings(scan["id"])
            if f["engine"] == "smells"
        }
        assert findings == {
            ("crp.arch.hub", f"{S}/hub/Hub.java", None),
            ("crp.arch.cycle", f"{S}/x/X.java", 3),
            ("crp.arch.cycle", f"{S}/y/Y.java", 3),
        }
        page = await stack.ok("GET", f"/v1/projects/{project}/issues?engine=smells&limit=50")
        issues = {(i["rule_id"], i["path"]): i for i in page["items"]}
        hub = issues[("crp.arch.hub", f"{S}/hub/Hub.java")]
        assert hub["title"] == "Hub-like part: com.acme.hub" and hub["status"] == "OPEN"

        second = await stack.zip_intake(project, _zip_of(CLEANED))
        await stack.scan_and_wait(project, second["snapshot_id"])
        page = await stack.ok("GET", f"/v1/projects/{project}/issues?engine=smells&limit=50")
        after = {i["id"]: i for i in page["items"]}
        # The hub's issue follows the part to its new first file instead of "fixed" + "new".
        moved = after[hub["id"]]
        assert (moved["path"], moved["status"]) == (f"{S}/hub/AHelper.java", "OPEN")
        assert moved["recheck_state"] == "VERIFIED_PRESENT" and len(after) == 3
        detail = await stack.ok("GET", f"/v1/issues/{hub['id']}")
        assert any(
            e["kind"] == "moved" and e["reason"] == "the part's anchor file changed"
            for e in detail["events"]
        )
        # The broken cycle is verified gone for both parts.
        for key in (("crp.arch.cycle", f"{S}/x/X.java"), ("crp.arch.cycle", f"{S}/y/Y.java")):
            gone = after[issues[key]["id"]]
            assert (gone["status"], gone["recheck_state"]) == ("RESOLVED", "VERIFIED_ABSENT")
