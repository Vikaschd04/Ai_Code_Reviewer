"""Project deletion: authorization, busy projects, origin checks and cascading removal."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from crp_core.artifacts import ArtifactKey, FilesystemArtifactStore
from crp_core.db.models import Intake, Membership, Project, User, Workspace
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_core.domain.states import MembershipRole, ProjectOrigin

from .conftest import WEB_ORIGIN, ApiFactory, ApiHarness
from .test_demo_and_sample import RecordingGateway

pytestmark = pytest.mark.integration


def _workspace(api: ApiHarness) -> str:
    assert api.identity is not None
    return str(api.identity.workspace_id)


async def _foreign_project(api: ApiHarness, role: MembershipRole) -> str:
    """A project created by someone else in a workspace where the local user has ``role``."""
    assert api.identity is not None
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            workspace_id, owner_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            await session.execute(
                insert(Workspace).values(
                    id=workspace_id, slug=f"t-{workspace_id.hex[:8]}", name="T"
                )
            )
            await session.execute(
                insert(User).values(id=owner_id, subject=f"other:{owner_id}", display_name="Other")
            )
            await session.execute(
                insert(Membership).values(workspace_id=workspace_id, user_id=owner_id, role="owner")
            )
            await session.execute(
                insert(Membership).values(
                    workspace_id=workspace_id, user_id=api.identity.user_id, role=role.value
                )
            )
            await session.execute(
                insert(Project).values(
                    id=project_id,
                    workspace_id=workspace_id,
                    slug="theirs",
                    name="Theirs",
                    origin=ProjectOrigin.USER.value,
                    created_by=owner_id,
                )
            )
            return str(project_id)
    finally:
        await engine.dispose()


async def _count(api: ApiHarness, model: type[object], project_id: str) -> int:
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            column = model.project_id if model is Intake else model.id  # type: ignore[attr-defined]
            value = await session.scalar(
                select(func.count()).select_from(model).where(column == uuid.UUID(project_id))  # type: ignore[arg-type]
            )
            return int(value or 0)
    finally:
        await engine.dispose()


async def test_creator_deletes_a_project_and_it_is_gone(api: ApiHarness) -> None:
    created = await api.client.post(
        "/v1/projects",
        json={"workspace_id": _workspace(api), "name": "Short-lived"},
        headers=api.auth,
    )
    project_id = created.json()["id"]
    deleted = await api.client.delete(f"/v1/projects/{project_id}", headers=api.auth)
    assert deleted.status_code == 204, deleted.text
    assert (await api.client.get(f"/v1/projects/{project_id}", headers=api.auth)).status_code == 404
    again = await api.client.delete(f"/v1/projects/{project_id}", headers=api.auth)
    assert again.status_code == 404
    assert await _count(api, Project, project_id) == 0


async def test_member_may_delete_only_own_projects_admin_any(api_factory: ApiFactory) -> None:
    api = await api_factory()
    as_member = await _foreign_project(api, MembershipRole.MEMBER)
    refused = await api.client.delete(f"/v1/projects/{as_member}", headers=api.auth)
    assert refused.status_code == 403
    assert refused.json()["code"] == "insufficient_role"
    as_viewer = await _foreign_project(api, MembershipRole.VIEWER)
    assert (
        await api.client.delete(f"/v1/projects/{as_viewer}", headers=api.auth)
    ).status_code == 403
    as_admin = await _foreign_project(api, MembershipRole.ADMIN)
    assert (
        await api.client.delete(f"/v1/projects/{as_admin}", headers=api.auth)
    ).status_code == 204


async def test_demo_user_cannot_delete_the_owner_project(api_factory: ApiFactory) -> None:
    api = await api_factory(demo_enabled=True)
    owner = await api.client.post(
        "/v1/projects", json={"workspace_id": _workspace(api), "name": "Owner's"}, headers=api.auth
    )
    await api.client.post("/v1/auth/demo-session", headers={"Origin": WEB_ORIGIN})
    attempt = await api.client.delete(
        f"/v1/projects/{owner.json()['id']}", headers={"Origin": WEB_ORIGIN}
    )
    assert attempt.status_code == 404  # existence is not revealed
    workspace = (await api.client.get("/v1/auth/me")).json()["workspaces"][0]["workspace_id"]
    mine = await api.client.post(
        "/v1/projects",
        json={"workspace_id": workspace, "name": "Demo"},
        headers={"Origin": WEB_ORIGIN},
    )
    no_origin = await api.client.delete(f"/v1/projects/{mine.json()['id']}")
    assert no_origin.status_code == 403 and no_origin.json()["code"] == "origin_rejected"
    ok = await api.client.delete(
        f"/v1/projects/{mine.json()['id']}", headers={"Origin": WEB_ORIGIN}
    )
    assert ok.status_code == 204


async def test_busy_project_is_refused_then_deleted_with_its_artifacts(
    api_factory: ApiFactory,
) -> None:
    api = await api_factory(gateway=RecordingGateway())
    sample = await api.client.post(
        "/v1/projects/sample", json={"workspace_id": _workspace(api)}, headers=api.auth
    )
    project_id, intake_id = sample.json()["project"]["id"], sample.json()["intake"]["id"]
    busy = await api.client.delete(f"/v1/projects/{project_id}", headers=api.auth)
    assert busy.status_code == 409 and busy.json()["code"] == "project_busy"

    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            intake = await session.get(Intake, uuid.UUID(intake_id))
            assert intake is not None
            intake.state = "FAILED"  # the (not running) validation gave up
            intake.error_code = "test"
            intake.error_message = "test double: validation did not run"
    finally:
        await engine.dispose()
    store = FilesystemArtifactStore(
        api.settings.artifact_root, max_object_bytes=api.settings.artifact_max_object_bytes
    )
    archive = ArtifactKey(f"intakes/{uuid.UUID(intake_id).hex}/archive.zip")
    assert store.exists(archive)
    deleted = await api.client.delete(f"/v1/projects/{project_id}", headers=api.auth)
    assert deleted.status_code == 204
    assert not store.exists(archive)
    assert await _count(api, Intake, project_id) == 0
