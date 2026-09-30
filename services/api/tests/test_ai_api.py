"""AI status, the per-project source-disclosure policy and the AI runs API.

No provider is called here: runs are only queued (a recording gateway stands in for the
workflow service). The worker tests run them end to end against a local fake provider.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from crp_core.db.models import AiCall, AiPolicyEvent, AiRun, FileEntry, Project, Snapshot, Source
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_core.domain.states import CaptureStatus, FileDisposition, SourceMode

from .conftest import WEB_ORIGIN, ApiFactory, ApiHarness
from .test_demo_and_sample import RecordingGateway

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
    listed = (await api.client.get("/v1/capabilities", headers=api.auth)).json()
    states = {c["id"]: c["state"] for c in listed["capabilities"]}
    assert states["ai_investigation"] == "available"


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


# -- runs --------------------------------------------------------------------------------------

AI_ON = {"ai_provider": "openai_compatible", "ai_model": "test-model", "ai_api_key": KEY}


async def _frozen_upload(api: ApiHarness, project: str, paths: dict[str, str]) -> str:
    """Insert a frozen upload with analyzable files (metadata only: nothing is analysed here)."""
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            project_id = uuid.UUID(project)
            row = await session.get(Project, project_id)
            assert row is not None
            workspace_id = row.workspace_id
            source = Source(
                workspace_id=workspace_id,
                project_id=project_id,
                mode=SourceMode.ZIP_UPLOAD.value,
                display_name="test.zip",
            )
            session.add(source)
            await session.flush()
            snapshot = Snapshot(
                workspace_id=workspace_id,
                project_id=project_id,
                source_id=source.id,
                capture_status=CaptureStatus.FROZEN.value,
                manifest_sha256="0" * 64,
                frozen_at=datetime.now(UTC),
                file_count=len(paths) + 1,
            )
            session.add(snapshot)
            await session.flush()
            for path, text in paths.items():
                session.add(
                    FileEntry(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        snapshot_id=snapshot.id,
                        path=path,
                        disposition=FileDisposition.ANALYZABLE.value,
                        blob_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        size_bytes=len(text),
                        line_count=text.count("\n") + 1,
                    )
                )
            session.add(
                FileEntry(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    snapshot_id=snapshot.id,
                    path="logo.png",
                    disposition=FileDisposition.EXCLUDED.value,
                    reason="binary",
                )
            )
            return str(snapshot.id)
    finally:
        await engine.dispose()


async def _enable(api: ApiHarness, project: str) -> None:
    enabled = await api.client.put(
        f"/v1/projects/{project}/ai-policy", json={"enabled": True}, headers=api.auth
    )
    assert enabled.status_code == 200, enabled.text


async def test_runs_are_refused_until_policy_provider_and_upload_allow_them(
    api_factory: ApiFactory,
) -> None:
    gateway = RecordingGateway()
    unconfigured = await api_factory(gateway=gateway)
    project = await _project(unconfigured)
    ask = {"kind": "question", "question": "Where is the order total computed?"}
    off = await unconfigured.client.post(
        f"/v1/projects/{project}/ai-runs", json=ask, headers=unconfigured.auth
    )
    assert off.status_code == 409 and off.json()["code"] == "ai_policy_disabled"
    await _enable(unconfigured, project)
    missing = await unconfigured.client.post(
        f"/v1/projects/{project}/ai-runs", json=ask, headers=unconfigured.auth
    )
    assert missing.status_code == 409 and missing.json()["code"] == "ai_unavailable"

    api = await api_factory(gateway=gateway, **AI_ON)
    no_upload = await api.client.post(f"/v1/projects/{project}/ai-runs", json=ask, headers=api.auth)
    assert no_upload.status_code == 409 and no_upload.json()["code"] == "no_snapshot"
    await _frozen_upload(api, project, {"src/app.py": "print('hi')\n"})
    bad = [
        ({"kind": "question"}, "question_required"),
        ({"kind": "file_review"}, "paths_required"),
        ({"kind": "finding_review"}, "finding_required"),
        ({"kind": "file_review", "paths": ["logo.png", "src/nope.py"]}, "unknown_paths"),
    ]
    for body, code in bad:
        refused = await api.client.post(
            f"/v1/projects/{project}/ai-runs", json=body, headers=api.auth
        )
        assert refused.status_code == 422 and refused.json()["code"] == code, (body, refused.text)
    unknown = await api.client.post(
        f"/v1/projects/{project}/ai-runs",
        json={"kind": "finding_review", "finding_id": str(uuid.uuid4())},
        headers=api.auth,
    )
    assert unknown.status_code == 404
    assert gateway.ai_runs == []  # nothing was queued by any refused request


async def test_run_lifecycle_through_the_api(api_factory: ApiFactory) -> None:
    gateway = RecordingGateway()
    api = await api_factory(gateway=gateway, **AI_ON)
    project = await _project(api)
    await _enable(api, project)
    snapshot = await _frozen_upload(api, project, {"src/app.py": "x = 1\n", "src/db.py": "y\n"})

    created = await api.client.post(
        f"/v1/projects/{project}/ai-runs",
        json={"kind": "file_review", "paths": ["src/db.py", "src/db.py", "src/app.py"]},
        headers=api.auth,
    )
    assert created.status_code == 202, created.text
    run = created.json()
    assert run["state"] == "QUEUED" and run["snapshot_id"] == snapshot
    assert run["target_paths"] == ["src/db.py", "src/app.py"]  # de-duplicated, order kept
    assert (run["provider"], run["model"], run["prompt_version"]) == (
        "openai_compatible",
        "test-model",
        "rx-ai-v1",
    )
    assert run["answer"] is None and run["findings"] == [] and run["usage"] is None
    assert gateway.ai_runs == [uuid.UUID(run["id"])]

    listed = (await api.client.get(f"/v1/projects/{project}/ai-runs", headers=api.auth)).json()
    assert [r["id"] for r in listed["items"]] == [run["id"]]
    fetched = await api.client.get(f"/v1/ai-runs/{run['id']}", headers=api.auth)
    assert fetched.status_code == 200 and fetched.json()["kind"] == "file_review"

    canceled = await api.client.post(f"/v1/ai-runs/{run['id']}/cancel", headers=api.auth)
    assert canceled.status_code == 200 and canceled.json()["cancel_requested_at"] is not None
    assert gateway.cancelled_ai_runs == [uuid.UUID(run["id"])]


async def test_runs_fail_visibly_when_the_workflow_service_is_down(api_factory: ApiFactory) -> None:
    api = await api_factory(**AI_ON)  # default gateway: unreachable
    project = await _project(api)
    await _enable(api, project)
    await _frozen_upload(api, project, {"a.py": "a = 1\n"})
    refused = await api.client.post(
        f"/v1/projects/{project}/ai-runs",
        json={"kind": "question", "question": "What does a.py do?"},
        headers=api.auth,
    )
    assert refused.status_code == 503 and refused.json()["code"] == "workflow_unavailable"
    runs = (await api.client.get(f"/v1/projects/{project}/ai-runs", headers=api.auth)).json()
    assert [(r["state"], r["error_code"]) for r in runs["items"]] == [
        ("FAILED", "workflow_unavailable")
    ]


async def test_monthly_token_limit_blocks_new_runs(api_factory: ApiFactory) -> None:
    api = await api_factory(gateway=RecordingGateway(), ai_monthly_token_limit=1000, **AI_ON)
    project = await _project(api)
    await _enable(api, project)
    await _frozen_upload(api, project, {"a.py": "a = 1\n"})
    ask = {"kind": "question", "question": "What does a.py do?"}
    first = await api.client.post(f"/v1/projects/{project}/ai-runs", json=ask, headers=api.auth)
    assert first.status_code == 202
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            session.add(
                AiCall(
                    run_id=uuid.UUID(first.json()["id"]),
                    sequence=1,
                    provider="openai_compatible",
                    model="test-model",
                    status="ok",
                    input_tokens=900,
                    output_tokens=200,
                )
            )
    finally:
        await engine.dispose()
    status = (await api.client.get("/v1/ai/status", headers=api.auth)).json()
    assert status["month"]["tokens"] == 1100
    limited = await api.client.post(f"/v1/projects/{project}/ai-runs", json=ask, headers=api.auth)
    assert limited.status_code == 429 and limited.json()["code"] == "ai_monthly_limit"


async def test_runs_of_other_workspaces_are_invisible(api_factory: ApiFactory) -> None:
    api = await api_factory(gateway=RecordingGateway(), demo_enabled=True, **AI_ON)
    project = await _project(api)
    await _enable(api, project)
    await _frozen_upload(api, project, {"a.py": "a = 1\n"})
    run = (
        await api.client.post(
            f"/v1/projects/{project}/ai-runs",
            json={"kind": "question", "question": "What does a.py do?"},
            headers=api.auth,
        )
    ).json()
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    for method, path in [
        ("GET", f"/v1/ai-runs/{run['id']}"),
        ("POST", f"/v1/ai-runs/{run['id']}/cancel"),
        ("GET", f"/v1/projects/{project}/ai-runs"),
        ("POST", f"/v1/projects/{project}/ai-runs"),
    ]:
        response = await api.client.request(
            method, path, headers=ORIGIN, json={"kind": "question", "question": "Leak it?"}
        )
        assert response.status_code == 404, (method, path, response.text)
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            stored = await session.get(AiRun, uuid.UUID(run["id"]))
            assert stored is not None and stored.cancel_requested_at is None
    finally:
        await engine.dispose()
