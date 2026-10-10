"""The built-in sample project on the real stack: a demo user creates it, the worker freezes it,
and every analyzer (real PMD, ESLint, Opengrep, Trivy) reports the problems planted in it."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from crp_core.config import Settings

pytestmark = pytest.mark.integration

StackFactory = Callable[..., contextlib.AbstractAsyncContextManager[Any]]
TERMINAL = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED"}
# Core checks succeed; the platform packs have no SAP/Salesforce files here, there is no
# deployment configuration, and the project has no architecture rules, and all say so.
EXPECTED_ENGINES = {
    **dict.fromkeys(
        ("structure", "graph", "pmd", "eslint", "opengrep", "trivy", "smells"), "SUCCEEDED"
    ),
    "pmd-apex": "NOT_APPLICABLE",
    "frameworks": "NOT_APPLICABLE",
    "nfr": "NOT_APPLICABLE",
    "architecture": "NOT_APPLICABLE",
}


async def _poll(client: httpx.AsyncClient, path: str, done: set[str], limit: int = 600) -> Any:
    for _ in range(limit):
        body = (await client.get(path)).json()
        if body["state"] in done:
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"timed out waiting for {path}")


async def test_demo_user_reviews_the_sample_project(
    settings: Settings, stack_factory: StackFactory
) -> None:
    demo_settings = settings.model_copy(update={"demo_enabled": True, "demo_max_scans_per_hour": 1})
    async with stack_factory(demo_settings) as stack:
        origin = {"Origin": demo_settings.allowed_web_origins[0]}
        async with httpx.AsyncClient(
            base_url=stack.server.url, headers=origin, timeout=30
        ) as guest:
            assert (await guest.post("/v1/auth/demo-session")).status_code == 200
            workspace = (await guest.get("/v1/auth/me")).json()["workspaces"][0]["workspace_id"]
            created = await guest.post("/v1/projects/sample", json={"workspace_id": workspace})
            assert created.status_code == 202, created.text
            project = created.json()["project"]["id"]
            intake = await _poll(
                guest, f"/v1/intakes/{created.json()['intake']['id']}", {"READY", "REJECTED"}
            )
            assert intake["state"] == "READY", intake
            body = {"snapshot_id": intake["snapshot_id"]}
            scan = await guest.post(f"/v1/projects/{project}/scans", json=body)
            assert scan.status_code == 202, scan.text
            limited = await guest.post(f"/v1/projects/{project}/scans", json=body)
            assert limited.status_code == 429
            assert limited.json()["code"] == "demo_limit_reached"

            done = await _poll(guest, f"/v1/scans/{scan.json()['id']}", TERMINAL)
            engines = {e["engine"]: e["state"] for e in done["engines"]}
            assert done["state"] == "SUCCEEDED", engines
            assert engines == EXPECTED_ENGINES, engines
            page = (await guest.get(f"/v1/scans/{done['id']}/findings?limit=200")).json()
            findings = page["items"]
            rules = {f["rule_id"] for f in findings}
            by_engine = {f["engine"] for f in findings}
            assert by_engine == {"pmd", "eslint", "opengrep", "trivy"}
            assert {
                "UseEqualsToCompareStrings",
                "EmptyCatchBlock",
                "CloseResource",
                "HardCodedCryptoKey",
                "no-eval",
                "no-dupe-keys",
                "use-isnan",
                "eqeqeq",
                "crp.java.sql-injection.string-concat",
                "crp.java.command-injection.runtime-exec",
                "crp.java.crypto.weak-hash",
                "crp.java.crypto.ecb-mode",
                "crp.js.xss.inner-html",
                "CVE-2021-44228",  # log4j-core 2.14.1 (pom.xml)
                "CVE-2021-23337",  # lodash 4.17.15 (web/package-lock.json)
                "secret:github-pat",  # generated fake token in web/src/config.js
            } <= rules, sorted(rules)


async def test_deleting_projects_reclaims_rows_artifacts_and_unshared_blobs(
    settings: Settings, stack_factory: StackFactory
) -> None:
    from sqlalchemy import func, select

    from crp_core.artifacts import FilesystemArtifactStore
    from crp_core.db.models import FileEntry, Finding, GraphBuild, Issue, Scan, Snapshot
    from crp_core.db.session import (
        create_engine_from_settings,
        create_session_factory,
        transaction,
    )

    store = FilesystemArtifactStore(
        settings.artifact_root, max_object_bytes=settings.artifact_max_object_bytes
    )
    async with stack_factory(settings) as stack:
        workspace = stack.workspace_id
        projects: list[tuple[str, str]] = []
        for _ in range(2):  # two projects with identical content share every blob
            created = await stack.ok(
                "POST", "/v1/projects/sample", json={"workspace_id": workspace}
            )
            intake = await stack.wait_intake(created["intake"]["id"])
            assert intake["state"] == "READY", intake
            projects.append((created["project"]["id"], intake["snapshot_id"]))
        first, first_snapshot = projects[0]
        done = await stack.scan_and_wait(first, first_snapshot)
        assert done["state"] == "SUCCEEDED"
        blobs_before = store.list_keys("blobs")
        assert blobs_before

        deleted = await stack.client.delete(f"/v1/projects/{first}")
        assert deleted.status_code == 204, deleted.text
        assert (await stack.client.get(f"/v1/projects/{first}")).status_code == 404
        assert store.list_keys(f"snapshots/{first_snapshot.replace('-', '')}") == []
        assert store.list_keys(f"scans/{done['id'].replace('-', '')}") == []
        assert store.list_keys("blobs") == blobs_before  # still used by the second project

        engine = create_engine_from_settings(settings)
        try:
            async with transaction(create_session_factory(engine)) as session:
                for model, column, value in (
                    (Snapshot, Snapshot.project_id, first),
                    (Scan, Scan.project_id, first),
                    (Issue, Issue.project_id, first),
                    (FileEntry, FileEntry.snapshot_id, first_snapshot),
                    (GraphBuild, GraphBuild.snapshot_id, first_snapshot),
                    (Finding, Finding.scan_id, done["id"]),
                ):
                    remaining = await session.scalar(
                        select(func.count()).select_from(model).where(column == value)
                    )
                    assert remaining == 0, model.__tablename__
        finally:
            await engine.dispose()

        second, second_snapshot = projects[1]
        still = await stack.ok("GET", f"/v1/snapshots/{second_snapshot}/files?limit=5")
        assert still["items"]
        assert (await stack.client.delete(f"/v1/projects/{second}")).status_code == 204
        assert store.list_keys("blobs") == []  # no snapshot uses them any more
