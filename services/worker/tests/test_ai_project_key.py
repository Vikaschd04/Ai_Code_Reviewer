"""A project's own AI model and key on the real stack (ADR 0026): the server has no AI set up;
an admin switches AI on, a wrong key is refused and nothing is stored, a working key is saved
only encrypted, the project's AI runs use it with Claude (thinking blocks handled), and removing
it stops AI; key tests are limited per hour."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from crp_core.config import Settings
from crp_core.db.models import ProjectAiModel
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction

from .conftest import Stack, lite_stack

pytestmark = pytest.mark.integration
GOOD = "sk-ant-good-user-key-0123456789"
BAD = "sk-ant-wrong-user-key-9876543210"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELED", "BUDGET_EXHAUSTED"}


class Anthropic:
    """Labelled test double of the Anthropic API for one user key."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.keys: list[str] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.anthropic.com", request.url
        key = request.headers.get("x-api-key", "")
        self.keys.append(key)
        if key != GOOD:
            return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})
        if request.method == "GET":
            assert request.url.path == "/v1/models/claude-opus-5-5"
            return httpx.Response(200, json={"id": "claude-opus-5-5", "type": "model"})
        self.messages.append(json.loads(request.content))
        answer = {
            "answer": "Not enough evidence to say who calls order_total.",
            "abstained": True,
            "uncertainty": "No caller exists in this upload.",
        }
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "model": "claude-opus-5-5",
                "content": [
                    {"type": "thinking", "thinking": "", "signature": "sig-1"},
                    {"type": "tool_use", "id": "toolu_1", "name": "submit_answer", "input": answer},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 900, "output_tokens": 60},
            },
        )


def _archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("src/orders.py", "def order_total(items):\n    return sum(items)\n")
    return buffer.getvalue()


async def _wait(stack: Stack, run_id: str) -> dict[str, Any]:
    for _ in range(300):
        run = await stack.ok("GET", f"/v1/ai-runs/{run_id}")
        if run["state"] in TERMINAL:
            return dict(run)
        await asyncio.sleep(0.2)
    raise AssertionError("AI run did not finish")


async def test_a_project_uses_its_own_tested_and_encrypted_key(settings: Settings) -> None:
    fake = Anthropic()
    own = settings.model_copy(  # no server AI; the fixed UnoRouter list
        update={"secrets_key": SecretStr("k" * 48), "ai_catalog_live": False}
    )
    async with lite_stack(own, httpx.MockTransport(fake.handle)) as stack:
        project = await stack.project("Own key")
        assert (await stack.zip_intake(project, _archive()))["state"] == "READY"
        url = f"/v1/projects/{project}/ai-model"
        catalog = await stack.ok("GET", "/v1/ai/catalog")
        assert catalog["can_store_keys"] is True
        providers = {p["id"]: p for p in catalog["providers"]}
        assert providers["anthropic"]["models"][0]["id"] == "claude-opus-5-5"
        assert {m["id"] for m in providers["unorouter"]["models"]} >= {"glm-5.3:free"}
        start = await stack.ok("GET", url)
        assert start["configured"] is False and start["can_edit"] is True

        body = {"provider": "anthropic", "model": "claude-opus-5-5", "api_key": GOOD}
        early = await stack.client.put(url, json=body)
        assert early.status_code == 409 and early.json()["code"] == "ai_policy_disabled"
        policy = await stack.ok("PUT", f"/v1/projects/{project}/ai-policy", json={"enabled": True})
        assert policy["enabled"] is True and policy["ready"] is False  # no model yet

        wrong = await stack.client.put(url, json={**body, "api_key": BAD})
        assert wrong.status_code == 422 and wrong.json()["code"] == "key_test_failed"
        assert "did not accept" in wrong.json()["message"] and BAD not in wrong.text
        assert (await stack.ok("GET", url))["configured"] is False  # nothing stored
        unknown = await stack.client.put(url, json={**body, "model": "claude-made-up"})
        assert unknown.status_code == 422 and unknown.json()["code"] == "unknown_model"

        saved = await stack.client.put(url, json=body)
        assert saved.status_code == 200, saved.text
        model = saved.json()
        assert model["configured"] and model["in_use"] and model["key_hint"] == GOOD[-4:]
        assert model["model_label"] == "Claude Opus 5.5 (Anthropic)" and GOOD not in saved.text
        engine = create_engine_from_settings(own)
        try:
            async with transaction(create_session_factory(engine)) as session:
                stored = (await session.scalars(select(ProjectAiModel.key_ciphertext))).all()
        finally:
            await engine.dispose()
        assert len(stored) == 1 and stored[0].startswith("v1.") and GOOD not in stored[0]
        policy = await stack.ok("GET", f"/v1/projects/{project}/ai-policy")
        assert policy["connection"] == "project" and policy["ready"] is True
        assert policy["connection_label"] == "Claude Opus 5.5 (Anthropic, your key)"

        run = await stack.ok(
            "POST",
            f"/v1/projects/{project}/ai-runs",
            json={"kind": "question", "question": "Who calls order_total?"},
        )
        assert (run["provider"], run["model"]) == ("anthropic", "claude-opus-5-5")
        done = await _wait(stack, run["id"])
        assert done["state"] == "SUCCEEDED", (done["error_code"], done["error_message"])
        assert done["answer"]["abstained"] is True
        assert fake.messages and fake.messages[0]["model"] == "claude-opus-5-5"
        assert set(fake.keys[-len(fake.messages) :]) == {GOOD}  # the project's own key

        removed = await stack.ok("DELETE", url)
        assert removed["configured"] is False
        policy = await stack.ok("GET", f"/v1/projects/{project}/ai-policy")
        assert policy["connection"] != "project" and policy["ready"] is False
        refused = await stack.client.post(
            f"/v1/projects/{project}/ai-runs",
            json={"kind": "question", "question": "Who calls order_total?"},
        )
        assert refused.status_code == 409 and refused.json()["code"] == "ai_unavailable"

        for _ in range(8):  # 2 tests so far (wrong and good; an unknown model is not tested)
            await stack.client.put(url, json={**body, "api_key": BAD})
        limited = await stack.client.put(url, json={**body, "api_key": BAD})
        assert limited.status_code == 429 and limited.json()["code"] == "key_test_limit"
