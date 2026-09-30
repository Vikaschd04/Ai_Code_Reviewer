"""AI status and the per-project source-disclosure policy (no provider is called here)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from crp_core.db.models import AiPolicyEvent
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction

from .conftest import WEB_ORIGIN, ApiFactory, ApiHarness

pytestmark = pytest.mark.integration
ORIGIN = {"Origin": WEB_ORIGIN}
KEY = "sk-test-key-that-is-long-enough-000000"


async def _project(api: ApiHarness) -> str:
    assert api.identity is not None
    created = await api.client.post(
        "/v1/projects",
        json={"workspace_id": str(api.identity.workspace_id), "name": "AI project"},
        headers=api.auth,
    )
    return str(created.json()["id"])


async def test_status_without_a_provider_is_actionable(api_factory: ApiFactory) -> None:
    api = await api_factory(demo_enabled=True)
    status = (await api.client.get("/v1/ai/status", headers=api.auth)).json()
    assert status["available"] is False and status["provider"] == "none"
    assert status["reason"] and "CRP_AI_PROVIDER" in status["admin_hint"]  # owner is operator
    assert status["month"]["calls"] == 0 and status["prices_configured"] is False
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    demo = (await api.client.get("/v1/ai/status")).json()
    assert demo["reason"] and demo["admin_hint"] is None  # setup details only for admins


async def test_status_with_a_configured_provider(api_factory: ApiFactory) -> None:
    api = await api_factory(
        ai_provider="anthropic",
        ai_api_key=KEY,
        ai_price_input_per_mtok_usd=3.0,
        ai_price_output_per_mtok_usd=15.0,
    )
    status = (await api.client.get("/v1/ai/status", headers=api.auth)).json()
    assert status["available"] is True and status["model"] == "claude-opus-5-5"
    assert status["reason"] is None and status["prices_configured"] is True
    assert status["month"]["cost_usd"] == 0.0 and status["month"]["token_limit"] > 0
    assert KEY not in str(status)


async def test_policy_is_off_by_default_and_audited_when_changed(api: ApiHarness) -> None:
    project = await _project(api)
    policy = (await api.client.get(f"/v1/projects/{project}/ai-policy", headers=api.auth)).json()
    assert policy["enabled"] is False and policy["version"] == 0 and policy["can_edit"] is True

    enabled = await api.client.put(
        f"/v1/projects/{project}/ai-policy",
        json={"enabled": True, "max_excerpt_lines": 80},
        headers=api.auth,
    )
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True
    version = enabled.json()["version"]
    stale = await api.client.put(
        f"/v1/projects/{project}/ai-policy",
        json={"enabled": False, "version": version + 5},
        headers=api.auth,
    )
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    disabled = await api.client.put(
        f"/v1/projects/{project}/ai-policy",
        json={"enabled": False, "version": version},
        headers=api.auth,
    )
    assert disabled.json()["enabled"] is False

    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            events = (await session.scalars(select(AiPolicyEvent).order_by(AiPolicyEvent.id))).all()
    finally:
        await engine.dispose()
    assert [e.changes.get("enabled") for e in events] == [[False, True], [True, False]]
    assert all(e.actor_user_id is not None for e in events)


async def test_members_cannot_enable_source_disclosure(api_factory: ApiFactory) -> None:
    api = await api_factory(demo_enabled=True)
    owner_project = await _project(api)
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    workspace = (await api.client.get("/v1/auth/me")).json()["workspaces"][0]["workspace_id"]
    mine = await api.client.post(
        "/v1/projects", json={"workspace_id": workspace, "name": "Demo AI"}, headers=ORIGIN
    )
    demo_project = mine.json()["id"]
    view = (await api.client.get(f"/v1/projects/{demo_project}/ai-policy")).json()
    assert view["enabled"] is False and view["can_edit"] is False
    refused = await api.client.put(
        f"/v1/projects/{demo_project}/ai-policy", json={"enabled": True}, headers=ORIGIN
    )
    assert refused.status_code == 403
    hidden = await api.client.get(f"/v1/projects/{owner_project}/ai-policy")
    assert hidden.status_code == 404
