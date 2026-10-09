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
        assert "crp-graph-extract-v2" in data["extractor"] and data["notes"]
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
