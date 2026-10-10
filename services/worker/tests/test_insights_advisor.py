"""Insights and the advisor agent on the real stack (labelled fake model through an HTTP mock
transport): the tools' recommendations come from a real review; the agent is off until the
project's admin switches AI on; its plan keeps only steps with known evidence and cited numbers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from crp_core.config import AiProvider, Settings
from crp_devtools.testing.fake_ai import LABEL, respond
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import Stack, lite_stack

pytestmark = pytest.mark.integration
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELED", "BUDGET_EXHAUSTED"}


class Model:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        return httpx.Response(200, json=respond(body))


def _settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "ai_provider": AiProvider.OPENAI_COMPATIBLE,
            "ai_model": "refactorx-e2e-fake",
            "ai_base_url": "https://provider.test/v1",
            "ai_api_key": SecretStr("sk-fake-provider-key-0123456789abcdef"),
        }
    )


async def _wait(stack: Stack, run_id: str) -> dict[str, Any]:
    for _ in range(600):
        run = await stack.ok("GET", f"/v1/ai-runs/{run_id}")
        if run["state"] in TERMINAL:
            return dict(run)
        await asyncio.sleep(0.3)
    raise AssertionError("advisor run did not finish")


async def test_insights_and_a_checked_advisor_plan(settings: Settings, tmp_path: Path) -> None:
    model = Model()
    async with lite_stack(
        _settings(settings), transport=httpx.MockTransport(model.handle)
    ) as stack:
        project = await stack.project("Insights")
        url = f"/v1/projects/{project}/insights"
        empty = await stack.ok("GET", url)
        assert empty["basis"] is None
        assert {r["kind"] for r in empty["recommendations"]} <= {"targets"}  # no review, no claims
        assert empty["advisor"]["can_request"] is False

        source = prepare_fixture("nfr-mixed", tmp_path / "nfr-mixed")
        intake = await stack.zip_intake(project, zip_directory(source))
        await stack.scan_and_wait(project, intake["snapshot_id"])
        data = await stack.ok("GET", url)
        recs = {r["id"]: r for r in data["recommendations"]}
        injection = recs["security.injection"]  # eval() in web/cart.js, seen by two tools
        assert injection["kind"] == "issues" and injection["issue_count"] == 2
        assert {i["path"] for i in injection["issues"]} == {"web/cart.js"} and injection["steps"]
        assert recs["reliability.defects"]["issue_count"] >= 1  # the empty catch
        # Mechanisms the upload shows produce no "missing" recommendation...
        for shown in ("reliability.tests", "reliability.fault-tolerance", "reliability.recovery"):
            assert shown not in recs
        # ...and the one it does not show does.
        assert recs["operations.diagnostics"]["kind"] == "missing"
        assert recs["targets.reliability"]["kind"] == "targets"
        areas = {a["id"]: a for a in data["areas"]}
        assert areas["security"]["state"] in {"attention", "improve"}
        assert areas["experience"]["state"] in {"no_problems", "unknown", "improve"}

        # The agent is off until an admin switches AI on: nothing is sent.
        assert data["advisor"]["enabled"] is False
        assert "switched off" in data["advisor"]["reason"]
        refused = await stack.client.post(f"{url}/plan")
        assert refused.status_code == 409 and refused.json()["code"] == "ai_policy_disabled"
        assert model.requests == []

        await stack.ok("PUT", f"/v1/projects/{project}/ai-policy", json={"enabled": True})
        data = await stack.ok("GET", url)
        assert data["advisor"]["can_request"] is True and data["advisor"]["reason"] is None
        run = await stack.ok("POST", f"{url}/plan")
        assert run["kind"] == "advisor" and run["state"] in {"QUEUED", "RUNNING"}
        done = await _wait(stack, run["id"])
        assert done["state"] == "SUCCEEDED", (done["error_code"], done["error_message"])
        plan = done["plan"]
        assert plan["summary"].startswith(LABEL)
        kept = [s["title"] for s in plan["steps"]]
        assert len(kept) == 2 and all(t.startswith(f"{LABEL} Start with:") for t in kept)
        assert all(s["insight_ids"] for s in plan["steps"])
        reasons = {r["title"]: r["reason"] for r in plan["rejected"]}
        assert reasons[f"{LABEL} Add a web application firewall"] == (
            "cites no known recommendation, fact or code"
        )
        assert reasons[f"{LABEL} Fix all 4242 issues"] == (
            "uses numbers not in the cited evidence: 4242"
        )
        assert any("Each step was checked" in note for note in done["limitations"])
        first = model.requests[0]
        message = next(m["content"] for m in first["messages"] if m["role"] == "user")
        assert "- F1: Recommendation" in message
        assert {t["function"]["name"] for t in first["tools"]} >= {"submit_plan", "read_file"}
        latest = (await stack.ok("GET", url))["advisor"]["latest"]
        assert latest["id"] == run["id"] and latest["plan"]["steps"]

        # "Start fixing": a workspace narrowed to the recommendation's issues.
        workspace = await stack.ok("POST", f"/v1/projects/{project}/change-sets", json={})
        page = await stack.ok(
            "GET",
            f"/v1/change-sets/{workspace['id']}/issues",
            params=[("issue", i) for i in injection["issue_ids"]],
        )
        assert page["total"] == 2 and {i["path"] for i in page["items"]} == {"web/cart.js"}
