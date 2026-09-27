"""Workspace-scoped project endpoints against a real PostgreSQL database."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from crp_core.db.models import Membership, Project, User, Workspace
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_core.domain.states import MembershipRole, ProjectOrigin

from .conftest import ApiHarness

pytestmark = pytest.mark.integration


async def _other_workspace(api: ApiHarness, *, local_role: MembershipRole | None) -> uuid.UUID:
    """Create a workspace owned by someone else, optionally granting the local user a role."""
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            workspace_id, owner_id = uuid.uuid4(), uuid.uuid4()
            await session.execute(
                insert(Workspace).values(
                    id=workspace_id, slug=f"other-{workspace_id.hex[:8]}", name="Other"
                )
            )
            await session.execute(
                insert(User).values(id=owner_id, subject=f"other:{owner_id}", display_name="Other")
            )
            await session.execute(
                insert(Membership).values(workspace_id=workspace_id, user_id=owner_id, role="owner")
            )
            await session.execute(
                insert(Project).values(
                    workspace_id=workspace_id,
                    slug="private",
                    name="Private project",
                    origin=ProjectOrigin.USER.value,
                )
            )
            if local_role is not None:
                assert api.identity is not None
                await session.execute(
                    insert(Membership).values(
                        workspace_id=workspace_id,
                        user_id=api.identity.user_id,
                        role=local_role.value,
                    )
                )
            return workspace_id
    finally:
        await engine.dispose()


def _local_workspace(api: ApiHarness) -> str:
    assert api.identity is not None
    return str(api.identity.workspace_id)


async def test_create_get_and_list_projects(api: ApiHarness) -> None:
    created = await api.client.post(
        "/v1/projects",
        json={"workspace_id": _local_workspace(api), "name": "Billing Service (Java)"},
        headers=api.auth,
    )
    assert created.status_code == 201
    project = created.json()
    assert project["slug"] == "billing-service-java"
    assert project["origin"] == "user"
    assert project["version"] == 1

    fetched = await api.client.get(f"/v1/projects/{project['id']}", headers=api.auth)
    assert fetched.status_code == 200
    assert fetched.json() == project

    listed = await api.client.get("/v1/projects", headers=api.auth)
    assert [p["id"] for p in listed.json()["items"]] == [project["id"]]


async def test_duplicate_slug_conflicts(api: ApiHarness) -> None:
    body = {"workspace_id": _local_workspace(api), "name": "Dup", "slug": "dup"}
    assert (await api.client.post("/v1/projects", json=body, headers=api.auth)).status_code == 201
    conflict = await api.client.post("/v1/projects", json=body, headers=api.auth)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "project_slug_conflict"


@pytest.mark.parametrize("slug", ["UPPER", "-lead", "trail-", "has space", "a" * 65])
async def test_invalid_slugs_are_rejected(api: ApiHarness, slug: str) -> None:
    response = await api.client.post(
        "/v1/projects",
        json={"workspace_id": _local_workspace(api), "name": "x", "slug": slug},
        headers=api.auth,
    )
    assert response.status_code == 400


async def test_other_workspaces_are_invisible(api: ApiHarness) -> None:
    other = await _other_workspace(api, local_role=None)
    create = await api.client.post(
        "/v1/projects", json={"workspace_id": str(other), "name": "Intrusion"}, headers=api.auth
    )
    assert create.status_code == 404
    assert create.json()["code"] == "workspace_not_found"
    listing = await api.client.get(f"/v1/projects?workspace_id={other}", headers=api.auth)
    assert listing.status_code == 404
    all_projects = await api.client.get("/v1/projects", headers=api.auth)
    assert all(p["workspace_id"] != str(other) for p in all_projects.json()["items"])


async def test_project_ids_from_other_workspaces_return_not_found(api: ApiHarness) -> None:
    other = await _other_workspace(api, local_role=None)
    engine = create_engine_from_settings(api.settings)
    try:
        async with engine.connect() as conn:
            foreign_id = (
                await conn.execute(select(Project.id).where(Project.workspace_id == other))
            ).scalar_one()
    finally:
        await engine.dispose()
    response = await api.client.get(f"/v1/projects/{foreign_id}", headers=api.auth)
    assert response.status_code == 404
    assert response.json()["code"] == "project_not_found"


async def test_viewer_cannot_create_projects(api: ApiHarness) -> None:
    viewer_ws = await _other_workspace(api, local_role=MembershipRole.VIEWER)
    visible = await api.client.get(f"/v1/projects?workspace_id={viewer_ws}", headers=api.auth)
    assert visible.status_code == 200
    assert [p["slug"] for p in visible.json()["items"]] == ["private"]
    create = await api.client.post(
        "/v1/projects", json={"workspace_id": str(viewer_ws), "name": "Nope"}, headers=api.auth
    )
    assert create.status_code == 403
    assert create.json()["code"] == "insufficient_role"


async def test_cursor_pagination_is_stable(api: ApiHarness) -> None:
    ids = []
    for index in range(3):
        response = await api.client.post(
            "/v1/projects",
            json={"workspace_id": _local_workspace(api), "name": f"Paged {index}"},
            headers=api.auth,
        )
        ids.append(response.json()["id"])
    first = (await api.client.get("/v1/projects?limit=2", headers=api.auth)).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = (
        await api.client.get(
            f"/v1/projects?limit=2&cursor={first['next_cursor']}", headers=api.auth
        )
    ).json()
    assert second["next_cursor"] is None
    assert [p["id"] for p in first["items"] + second["items"]] == ids


async def test_malformed_cursor_is_a_client_error(api: ApiHarness) -> None:
    response = await api.client.get("/v1/projects?cursor=%%%bad", headers=api.auth)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_cursor"
