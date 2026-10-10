"""NFR readiness API (P12 slice 1): members save profiles, other workspaces see nothing, and a
project without a reviewed upload claims no code evidence."""

from __future__ import annotations

import pytest

from .conftest import WEB_ORIGIN, ApiFactory

pytestmark = pytest.mark.integration
ORIGIN = {"Origin": WEB_ORIGIN}


async def test_members_save_profiles_and_other_workspaces_see_nothing(
    api_factory: ApiFactory,
) -> None:
    api = await api_factory(demo_enabled=True)
    assert api.identity is not None
    owner = await api.client.post(
        "/v1/projects",
        json={"workspace_id": str(api.identity.workspace_id), "name": "Owner NFR"},
        headers=api.auth,
    )
    owner_project = owner.json()["id"]
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    workspace = (await api.client.get("/v1/auth/me")).json()["workspaces"][0]["workspace_id"]
    mine = await api.client.post(
        "/v1/projects", json={"workspace_id": workspace, "name": "Demo NFR"}, headers=ORIGIN
    )
    project = mine.json()["id"]
    view = (await api.client.get(f"/v1/projects/{project}/nfr")).json()
    assert view["can_edit"] is True and view["basis"] is None
    statuses = {q["status"] for a in view["aspects"] for q in a["questions"]}
    assert statuses <= {"not_checked", "needs_input"}  # no upload: no evidence, no claims
    saved = await api.client.put(
        f"/v1/projects/{project}/nfr/profile",
        json={
            "document": {"targets": {"peak_concurrent_users": 200}, "platforms": ["Linux"]},
            "base_version": 0,
        },
        headers=ORIGIN,
    )
    assert saved.status_code == 200 and saved.json()["profile_version"] == 1
    same = await api.client.put(
        f"/v1/projects/{project}/nfr/profile",
        json={
            "document": {"targets": {"peak_concurrent_users": 200}, "platforms": ["Linux"]},
            "base_version": 1,
        },
        headers=ORIGIN,
    )
    assert same.json()["profile_version"] == 1  # identical profiles add no version
    out_of_range = await api.client.put(
        f"/v1/projects/{project}/nfr/profile",
        json={"document": {"targets": {"availability_percent": 101}}, "base_version": 1},
        headers=ORIGIN,
    )
    assert out_of_range.status_code == 400  # request validation (range) is a 400 here
    assert (await api.client.get(f"/v1/projects/{owner_project}/nfr")).status_code == 404
    refused = await api.client.put(
        f"/v1/projects/{owner_project}/nfr/profile",
        json={"document": {}, "base_version": 0},
        headers=ORIGIN,
    )
    assert refused.status_code == 404
    hidden = await api.client.get(f"/v1/projects/{owner_project}/nfr/export?format=md")
    assert hidden.status_code == 404
