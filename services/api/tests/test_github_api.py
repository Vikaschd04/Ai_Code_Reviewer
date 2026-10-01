"""GitHub API boundaries (P06): webhook authentication and idempotency, verified linking, roles,
the demo account and other workspaces. The labelled fake GitHub stands in for github.com; no
workflow runs here (reviews are only recorded)."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from crp_core.db.models import CodeReview, GitDelivery, GitLinkRequest, User
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_devtools.testing.fake_github import FakeGitHub, generate_app_key

from .conftest import ApiFactory, ApiHarness
from .test_demo_and_sample import ORIGIN, RecordingGateway

pytestmark = pytest.mark.integration
SECRET = "whsec-api-tests-0123456789"


@pytest.fixture(scope="module")
def keys() -> tuple[bytes, bytes]:
    return generate_app_key()


@pytest.fixture
def fake(keys: tuple[bytes, bytes]) -> Iterator[FakeGitHub]:
    github = FakeGitHub(
        public_key_pem=keys[1], callback_url="http://127.0.0.1:1/v1/github/callback"
    )
    github.start()
    repo = github.add_repo(501, "acme", "api")
    repo.commit("main", {"src/App.java": "class App {}\n"})
    github.add_installation(601, "acme", [501])
    github.add_installation(602, "other", [])
    github.add_user("admin-user", [601])
    try:
        yield github
    finally:
        github.stop()


async def _api(
    api_factory: ApiFactory, fake: FakeGitHub, keys: tuple[bytes, bytes], **extra: Any
) -> ApiHarness:
    return await api_factory(
        gateway=RecordingGateway(),
        github_app_id=fake.app_id,
        github_client_id=fake.client_id,
        github_client_secret=fake.client_secret,
        github_private_key=keys[0].decode(),
        github_webhook_secret=SECRET,
        github_app_slug=fake.slug,
        github_api_url=fake.base_url,
        github_web_url=fake.base_url,
        **extra,
    )


def _workspace(api: ApiHarness) -> str:
    assert api.identity is not None
    return str(api.identity.workspace_id)


async def _webhook(api: ApiHarness, event: str, payload: dict[str, Any], **headers: str) -> Any:
    body = json.dumps(payload).encode()
    base = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": headers.pop("delivery", uuid.uuid4().hex),
        "X-Hub-Signature-256": FakeGitHub.sign(SECRET, body),
    }
    return await api.client.post("/v1/github/webhook", content=body, headers={**base, **headers})


async def _count(api: ApiHarness, model: Any) -> int:
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            return int(await session.scalar(select(func.count()).select_from(model)) or 0)
    finally:
        await engine.dispose()


async def _link(api: ApiHarness, fake: FakeGitHub) -> dict[str, Any]:
    workspace = _workspace(api)
    start = await api.client.post(f"/v1/workspaces/{workspace}/github/link", headers=api.auth)
    assert start.status_code == 200, start.text
    consent = fake.handle("GET", start.json()["authorize_url"].removeprefix(fake.base_url), {}, b"")
    location = consent[1]["Location"]
    callback = await api.client.get(location.removeprefix("http://127.0.0.1:1"))
    assert callback.status_code == 303
    fragment = callback.headers["location"].split("#", 1)[1]
    query = dict(part.split("=", 1) for part in fragment.split("?", 1)[1].split("&"))
    done = await api.client.post(
        f"/v1/workspaces/{workspace}/github/link/complete",
        json={"code": query["code"], "state": query["state"]},
        headers=api.auth,
    )
    assert done.status_code == 200, done.text
    return dict(done.json())


async def test_webhooks_need_a_valid_signature_and_are_idempotent(
    api_factory: ApiFactory, fake: FakeGitHub, keys: tuple[bytes, bytes]
) -> None:
    unconfigured = await api_factory()
    off = await unconfigured.client.post("/v1/github/webhook", content=b"{}")
    assert off.status_code == 503 and off.json()["code"] == "webhooks_not_configured"
    api = await _api(api_factory, fake, keys)
    forged = await _webhook(
        api, "ping", {"zen": "x"}, **{"X-Hub-Signature-256": "sha256=" + "0" * 64}
    )
    assert forged.status_code == 401 and forged.json()["code"] == "invalid_signature"
    unsigned = await api.client.post(
        "/v1/github/webhook",
        content=b"{}",
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": uuid.uuid4().hex},
    )
    assert unsigned.status_code == 401
    assert await _count(api, GitDelivery) == 0  # nothing is recorded for unauthenticated calls
    malformed = await _webhook(api, "ping", {}, delivery="../../etc")
    assert malformed.status_code == 400
    big = await _api(api_factory, fake, keys, github_webhook_max_bytes=2048)
    oversized = await _webhook(big, "ping", {"padding": "x" * 4096})
    assert oversized.status_code == 413
    pong = await _webhook(api, "ping", {"zen": "x"}, delivery="delivery-ping-1")
    assert pong.status_code == 200 and pong.json()["outcome"] == "ok"
    repeat = await _webhook(api, "ping", {"zen": "x"}, delivery="delivery-ping-1")
    assert repeat.json()["outcome"] == "duplicate"
    stranger = await _webhook(
        api,
        "push",
        {
            "ref": "refs/heads/main",
            "after": "a" * 40,
            "installation": {"id": 999},
            "repository": {"id": 1},
        },
    )
    assert stranger.json()["outcome"] == "ignored" and "not linked" in stranger.json()["detail"]
    assert await _count(api, CodeReview) == 0


async def test_linking_is_verified_and_bound_to_the_admin(
    api_factory: ApiFactory, fake: FakeGitHub, keys: tuple[bytes, bytes]
) -> None:
    api = await _api(api_factory, fake, keys, demo_enabled=True)
    status = (await api.client.get("/v1/github/status", headers=api.auth)).json()
    assert status["available"] and status["linking_available"]
    assert status["install_url"] == f"{fake.base_url}/apps/{fake.slug}/installations/new"
    setup = await api.client.get("/v1/github/setup?installation_id=602&setup_action=install")
    assert setup.status_code == 303 and setup.headers["location"].endswith("#/github?installed=1")
    linked = await _link(api, fake)
    # Only what the GitHub user can access is linked; the forged installation id is ignored.
    assert [i["account"] for i in linked["linked"]] == ["acme"]
    assert [r["full_name"] for r in linked["linked"][0]["repositories"]] == ["acme/api"]
    workspace = _workspace(api)
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            other = User(subject=f"other-{uuid.uuid4().hex}", display_name="Other")
            session.add(other)
            await session.flush()
            for state, user, expires in (
                (
                    "someone-elses-state-000000000",
                    other.id,
                    datetime.now(UTC) + timedelta(minutes=5),
                ),
                (
                    "expired-state-0000000000000000",
                    api.identity.user_id if api.identity else other.id,
                    datetime.now(UTC) - timedelta(minutes=1),
                ),
            ):
                session.add(
                    GitLinkRequest(
                        workspace_id=uuid.UUID(workspace),
                        user_id=user,
                        state_sha256=hashlib.sha256(state.encode()).hexdigest(),
                        expires_at=expires,
                    )
                )
    finally:
        await engine.dispose()
    path = f"/v1/workspaces/{workspace}/github/link/complete"
    theirs = await api.client.post(
        path, json={"code": "x", "state": "someone-elses-state-000000000"}, headers=api.auth
    )
    assert theirs.status_code == 403 and theirs.json()["code"] == "link_state_invalid"
    expired = await api.client.post(
        path, json={"code": "x", "state": "expired-state-0000000000000000"}, headers=api.auth
    )
    assert expired.status_code == 409 and expired.json()["code"] == "link_state_expired"
    # The demo account (a member of its own workspace) can neither link nor see this workspace.
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    me = (await api.client.get("/v1/auth/me")).json()
    demo_workspace = me["workspaces"][0]["workspace_id"]
    refused = await api.client.post(f"/v1/workspaces/{demo_workspace}/github/link", headers=ORIGIN)
    assert refused.status_code == 403 and refused.json()["code"] == "demo_not_allowed"
    hidden = await api.client.get(f"/v1/workspaces/{workspace}/github/installations")
    assert hidden.status_code == 404
    demo_status = (await api.client.get("/v1/github/status")).json()
    assert demo_status["admin_hint"] is None


async def test_connections_are_admin_managed_and_scoped(
    api_factory: ApiFactory, fake: FakeGitHub, keys: tuple[bytes, bytes]
) -> None:
    api = await _api(api_factory, fake, keys, demo_enabled=True)
    linked = await _link(api, fake)
    repo_id = linked["linked"][0]["repositories"][0]["id"]
    workspace = _workspace(api)
    project = (
        await api.client.post(
            "/v1/projects", json={"workspace_id": workspace, "name": "API"}, headers=api.auth
        )
    ).json()["id"]
    empty = (
        await api.client.get(f"/v1/projects/{project}/git-connection", headers=api.auth)
    ).json()
    assert empty == {**empty, "connected": False, "can_edit": True}
    connected = await api.client.put(
        f"/v1/projects/{project}/git-connection", json={"repository_id": repo_id}, headers=api.auth
    )
    assert connected.status_code == 201, connected.text
    body = connected.json()
    assert body["publish_checks"] is False and body["publish_pull_requests"] is False
    assert body["review_forks"] is False  # safe defaults
    gateway = api.app.state.container.workflows
    reviews = (
        await api.client.get(f"/v1/projects/{project}/code-reviews", headers=api.auth)
    ).json()
    assert [r["trigger"] for r in reviews["items"]] == ["manual"]
    assert gateway.git_reviews == [uuid.UUID(reviews["items"][0]["id"])]
    second = (
        await api.client.post(
            "/v1/projects", json={"workspace_id": workspace, "name": "API 2"}, headers=api.auth
        )
    ).json()["id"]
    taken = await api.client.put(
        f"/v1/projects/{second}/git-connection", json={"repository_id": repo_id}, headers=api.auth
    )
    assert taken.status_code == 409 and taken.json()["code"] == "repository_connected"
    # Push and pull request events for the connected repository create reviews (recorded only).
    pushed = await _webhook(
        api,
        "push",
        {
            "ref": "refs/heads/main",
            "after": "b" * 40,
            "installation": {"id": 601},
            "repository": {"id": 501},
        },
    )
    assert pushed.status_code == 202 and pushed.json()["outcome"] == "accepted"
    fork = await _webhook(
        api,
        "pull_request",
        {
            "action": "opened",
            "number": 3,
            "pull_request": {"number": 3, "head": {"repo": {"id": 777}}},
            "installation": {"id": 601},
            "repository": {"id": 501},
        },
    )
    assert fork.json()["outcome"] == "ignored" and "fork" in fork.json()["detail"]
    # Other workspaces see nothing; the demo account cannot change settings.
    review_id = reviews["items"][0]["id"]
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    for method, path in (
        ("GET", f"/v1/projects/{project}/git-connection"),
        ("GET", f"/v1/projects/{project}/code-reviews"),
        ("GET", f"/v1/code-reviews/{review_id}"),
        ("POST", f"/v1/code-reviews/{review_id}/cancel"),
        ("POST", f"/v1/projects/{project}/code-reviews"),
    ):
        response = await api.client.request(method, path, headers=ORIGIN, json={})
        assert response.status_code == 404, (method, path, response.text)
    demo = await api.client.patch(
        f"/v1/projects/{project}/git-connection",
        json={"version": 1, "publish_checks": True},
        headers=ORIGIN,
    )
    assert demo.status_code == 403
    uninstall = fake.installation_payload("deleted", fake.installations[601])
    gone = await _webhook(api, "installation", uninstall)
    assert gone.json()["outcome"] == "accepted"
    status = (
        await api.client.get(f"/v1/projects/{project}/git-connection", headers=api.auth)
    ).json()
    assert status["status"] == "installation_revoked"
    assert gateway.cancelled_git_reviews  # active reviews were stopped
