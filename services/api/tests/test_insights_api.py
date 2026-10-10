"""Insights API (ADR 0024): without a review nothing is claimed; members record "handled
elsewhere" for missing mechanisms only; other workspaces see and change nothing; the retired
questionnaire endpoints are gone."""

from __future__ import annotations

import pytest

from .conftest import WEB_ORIGIN, ApiFactory

pytestmark = pytest.mark.integration
ORIGIN = {"Origin": WEB_ORIGIN}


async def test_checkpoint_decisions_and_isolation(api_factory: ApiFactory) -> None:
    api = await api_factory(demo_enabled=True)
    assert api.identity is not None
    owner = await api.client.post(
        "/v1/projects",
        json={"workspace_id": str(api.identity.workspace_id), "name": "Owner insights"},
        headers=api.auth,
    )
    owner_project = owner.json()["id"]
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    workspace = (await api.client.get("/v1/auth/me")).json()["workspaces"][0]["workspace_id"]
    mine = await api.client.post(
        "/v1/projects", json={"workspace_id": workspace, "name": "Demo insights"}, headers=ORIGIN
    )
    project = mine.json()["id"]
    url = f"/v1/projects/{project}/insights"
    view = (await api.client.get(url)).json()
    assert view["basis"] is None and view["can_edit"] is True
    assert {c["status"] for c in view["checkpoints"]} == {"not_checked"}  # no review, no claims
    assert {a["state"] for a in view["areas"]} == {"unknown"}
    assert view["advisor"]["can_request"] is False

    handled = await api.client.put(
        f"{url}/checkpoints/operations.monitoring/handled",
        json={"reason": "Datadog agent on every node"},
        headers=ORIGIN,
    )
    assert handled.status_code == 200 and handled.json()["decisions_version"] == 1
    same = await api.client.put(
        f"{url}/checkpoints/operations.monitoring/handled",
        json={"reason": "Datadog   agent on every node"},
        headers=ORIGIN,
    )
    assert same.json()["decisions_version"] == 1  # the same decision adds no version
    refused = await api.client.put(
        f"{url}/checkpoints/security.injection/handled",
        json={"reason": "we are careful"},
        headers=ORIGIN,
    )
    assert refused.status_code == 422 and refused.json()["code"] == "not_decidable"
    unknown = await api.client.put(
        f"{url}/checkpoints/made.up/handled", json={"reason": "x y z"}, headers=ORIGIN
    )
    assert unknown.status_code == 404 and unknown.json()["code"] == "checkpoint_not_found"
    short = await api.client.put(
        f"{url}/checkpoints/operations.monitoring/handled", json={"reason": "no"}, headers=ORIGIN
    )
    assert short.status_code == 400  # request validation (length)
    cleared = await api.client.delete(
        f"{url}/checkpoints/operations.monitoring/handled", headers=ORIGIN
    )
    assert cleared.status_code == 200 and cleared.json()["decisions_version"] == 2

    assert (await api.client.get(f"/v1/projects/{owner_project}/insights")).status_code == 404
    other = await api.client.put(
        f"/v1/projects/{owner_project}/insights/checkpoints/operations.monitoring/handled",
        json={"reason": "not mine"},
        headers=ORIGIN,
    )
    assert other.status_code == 404
    assert (await api.client.get(f"/v1/projects/{project}/nfr")).status_code == 404
