"""Hosted single-user mode (behind a TLS proxy) and direct-upload tickets, on real PostgreSQL."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from .conftest import ApiFactory, ApiHarness

pytestmark = pytest.mark.integration

HOSTED: dict[str, Any] = {
    "environment": "hosted",
    "api_host": "0.0.0.0",  # noqa: S104 - hosted mode binds publicly behind a TLS proxy
    "public_hosts": ("api.example.com",),
    "allowed_web_origins": ("https://app.example.com",),
    "allowed_web_origin_regex": r"^https://app-[a-z0-9-]+\.example\.com$",
    "public_api_url": "https://api.example.com",
    "base_url": "https://api.example.com",
    "client_addr": ("203.0.113.9", 44321),
}


async def _intake(api: ApiHarness, origin: dict[str, str] | None = None) -> str:
    assert api.identity is not None
    project = await api.client.post(
        "/v1/projects",
        json={
            "workspace_id": str(api.identity.workspace_id),
            "name": f"Hosted {uuid.uuid4().hex[:8]}",
        },
        headers=api.auth,
    )
    assert project.status_code == 201, project.text
    intake = await api.client.post(
        f"/v1/projects/{project.json()['id']}/intakes",
        json={"mode": "zip_upload", "display_name": "upload.zip"},
        headers=api.auth,
    )
    assert intake.status_code == 201, intake.text
    return str(intake.json()["id"])


async def test_hosted_mode_serves_configured_host_from_non_loopback_peers(
    api_factory: ApiFactory,
) -> None:
    api = await api_factory(**HOSTED)
    response = await api.client.get("/v1/health/live")
    assert response.status_code == 200
    assert response.headers["strict-transport-security"].startswith("max-age=")
    me = await api.client.get("/v1/auth/me", headers=api.auth)
    assert me.status_code == 200


async def test_hosted_mode_rejects_unknown_hosts_except_liveness(api_factory: ApiFactory) -> None:
    api = await api_factory(**{**HOSTED, "base_url": "https://evil.example.com"})
    blocked = await api.client.get("/v1/auth/me", headers=api.auth)
    assert blocked.status_code == 403 and blocked.json()["code"] == "host_not_allowed"
    assert (await api.client.get("/v1/health/live")).status_code == 200


async def test_hosted_sessions_use_secure_cookies_and_allowed_origins(
    api_factory: ApiFactory,
) -> None:
    api = await api_factory(**HOSTED)
    for origin in ("https://app.example.com", "https://app-git-main.example.com"):
        ok = await api.client.post(
            "/v1/auth/session", json={"token": api.token}, headers={"Origin": origin}
        )
        assert ok.status_code == 200, (origin, ok.text)
        cookie = ok.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    denied = await api.client.post(
        "/v1/auth/session",
        json={"token": api.token},
        headers={"Origin": "https://app.example.com.evil.net"},
    )
    assert denied.status_code == 403


async def test_cors_preflight_allows_only_web_origins_for_uploads(api_factory: ApiFactory) -> None:
    api = await api_factory(**HOSTED)
    preflight = {
        "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "content-type",
    }
    ok = await api.client.options(
        "/v1/intakes/00000000-0000-0000-0000-000000000000/content",
        headers={"Origin": "https://app.example.com", **preflight},
    )
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "https://app.example.com"
    assert "access-control-allow-credentials" not in ok.headers
    bad = await api.client.options(
        "/v1/intakes/00000000-0000-0000-0000-000000000000/content",
        headers={"Origin": "https://evil.example.com", **preflight},
    )
    assert "access-control-allow-origin" not in bad.headers


@pytest.mark.parametrize("hosted", [False, True])
async def test_upload_tickets_authorize_exactly_one_intake(
    api_factory: ApiFactory, hosted: bool
) -> None:
    api = await api_factory(**HOSTED) if hosted else await api_factory()
    intake_id = await _intake(api)
    other_id = await _intake(api)
    issued = await api.client.post(f"/v1/intakes/{intake_id}/upload-ticket", headers=api.auth)
    assert issued.status_code == 200, issued.text
    body = issued.json()
    url = urlsplit(body["upload_url"])
    if hosted:
        assert f"{url.scheme}://{url.netloc}" == "https://api.example.com"
    else:
        assert not url.netloc  # same origin locally
    assert body["max_bytes"] > 0
    zip_headers = {"Content-Type": "application/zip"}
    ticket = url.query.removeprefix("ticket=")
    uploaded = await api.client.put(
        f"{url.path}?{url.query}", content=b"PK\x05\x06" + b"\0" * 18, headers=zip_headers
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["state"] == "UPLOADING"
    wrong = await api.client.put(
        f"/v1/intakes/{other_id}/content?ticket={ticket}", content=b"x", headers=zip_headers
    )
    assert wrong.status_code == 401
    tampered = await api.client.put(
        f"{url.path}?ticket={ticket[:-2]}xx", content=b"x", headers=zip_headers
    )
    assert tampered.status_code == 401
    missing = await api.client.put(url.path, content=b"x", headers=zip_headers)
    assert missing.status_code == 401
    identity = api.app.state.container.identity
    expired, _ = identity.issue_upload_ticket(intake_id, now=time.time() - 3600)
    old = await api.client.put(f"{url.path}?ticket={expired}", content=b"x", headers=zip_headers)
    assert old.status_code == 401


async def test_upload_ticket_requires_member_access(api_factory: ApiFactory) -> None:
    api = await api_factory()
    intake_id = await _intake(api)
    anonymous = await api.client.post(f"/v1/intakes/{intake_id}/upload-ticket")
    assert anonymous.status_code == 401


async def test_container_serves_the_web_ui_on_the_api_origin(
    api_factory: ApiFactory, tmp_path: Path
) -> None:
    web = tmp_path / "web-dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text('<!doctype html><div id="root"></div>')
    (web / "assets" / "index-abc123.js").write_text("console.log('ok')")
    api = await api_factory(**HOSTED, web_static_dir=web)
    page = await api.client.get("/")
    assert page.status_code == 200 and 'id="root"' in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-cache"
    asset = await api.client.get("/assets/index-abc123.js")
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]
    live = await api.client.get("/v1/health/live")  # API routes win over the UI mount
    assert live.headers["content-type"].startswith("application/json")
    me = await api.client.get("/v1/auth/me", headers=api.auth)
    assert me.status_code == 200
    assert (await api.client.get("/assets/missing.js")).status_code == 404
    other_host = await api_factory(
        **{**HOSTED, "base_url": "https://evil.example.com"}, web_static_dir=web
    )
    assert (await other_host.client.get("/")).status_code == 403  # Host allowlist covers the UI
