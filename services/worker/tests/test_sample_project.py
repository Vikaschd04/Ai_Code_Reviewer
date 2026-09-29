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
            assert all(state == "SUCCEEDED" for state in engines.values()), engines
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
