"""Health, readiness and authentication behaviour against a real PostgreSQL database."""

from __future__ import annotations

import time

import pytest

from crp_api.auth.local_token import SESSION_COOKIE, LocalTokenProvider
from crp_core.db.migrate import head_revision

from .conftest import WEB_ORIGIN, ApiFactory, ApiHarness

pytestmark = pytest.mark.integration


def _assert_error(body: dict[str, object], code: str) -> None:
    assert body["code"] == code
    assert isinstance(body["message"], str)
    assert isinstance(body["request_id"], str)
    assert len(body["request_id"]) == 32
    assert "Traceback" not in str(body)


async def test_liveness_is_public_and_carries_security_headers(api: ApiHarness) -> None:
    response = await api.client.get("/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


async def test_readiness_requires_authentication(api: ApiHarness) -> None:
    response = await api.client.get("/v1/health/ready")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    _assert_error(response.json(), "authentication_required")


async def test_wrong_bearer_token_is_rejected(api: ApiHarness) -> None:
    response = await api.client.get(
        "/v1/health/ready", headers={"Authorization": "Bearer not-the-token"}
    )
    assert response.status_code == 401


async def test_readiness_reports_real_dependency_states(api: ApiHarness) -> None:
    response = await api.client.get("/v1/health/ready", headers=api.auth)
    assert response.status_code == 503
    body = response.json()
    checks = {check["name"]: check for check in body["checks"]}
    assert body["status"] == "not_ready"
    assert checks["database"]["status"] == "ok"
    assert checks["database"]["details"]["schema_revision"] == head_revision()
    assert checks["artifact_store"]["status"] == "ok"
    assert checks["workflow_service"]["status"] == "unavailable"
    assert checks["workflow_service"]["error_code"] == "workflow_service_unreachable"
    assert checks["workflow_worker"]["status"] == "unavailable"


async def test_readiness_detects_unmigrated_schema(
    api_factory: ApiFactory, empty_database_url: str
) -> None:
    harness = await api_factory(database_url=empty_database_url, provision=False)
    response = await harness.client.get("/v1/health/ready", headers=harness.auth)
    assert response.status_code == 503
    database = next(c for c in response.json()["checks"] if c["name"] == "database")
    assert database["status"] == "failed"
    assert database["error_code"] == "schema_out_of_date"
    assert database["details"]["schema_revision"] is None
    # Data endpoints fail with a structured dependency error rather than a 500.
    me = await harness.client.get("/v1/auth/me", headers=harness.auth)
    assert me.status_code == 503
    _assert_error(me.json(), "database_unavailable")


async def test_readiness_detects_unreachable_database(api_factory: ApiFactory) -> None:
    harness = await api_factory(
        database_url="postgresql+psycopg://nobody:none@127.0.0.1:1/none",
        provision=False,
        database_connect_timeout_seconds=1.0,
    )
    response = await harness.client.get("/v1/health/ready", headers=harness.auth)
    assert response.status_code == 503
    database = next(c for c in response.json()["checks"] if c["name"] == "database")
    assert database["status"] == "unavailable"
    assert database["error_code"] == "database_unreachable"
    assert "nobody" not in response.text


async def test_identity_must_be_provisioned(api_factory: ApiFactory) -> None:
    harness = await api_factory(provision=False)
    response = await harness.client.get("/v1/auth/me", headers=harness.auth)
    assert response.status_code == 403
    _assert_error(response.json(), "identity_not_provisioned")


async def test_session_cookie_flow_and_origin_enforcement(api: ApiHarness) -> None:
    origin = {"Origin": WEB_ORIGIN}
    bad = await api.client.post("/v1/auth/session", json={"token": "wrong"}, headers=origin)
    assert bad.status_code == 401
    _assert_error(bad.json(), "invalid_credentials")

    good = await api.client.post("/v1/auth/session", json={"token": api.token}, headers=origin)
    assert good.status_code == 200
    cookie_header = good.headers["set-cookie"]
    assert SESSION_COOKIE in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=strict" in cookie_header
    assert api.token not in cookie_header

    me = await api.client.get("/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["auth_method"] == "session_cookie"
    assert me.json()["is_operator"] is True

    workspace_id = me.json()["workspaces"][0]["workspace_id"]
    payload = {"workspace_id": workspace_id, "name": "Cookie project"}
    no_origin = await api.client.post("/v1/projects", json=payload)
    assert no_origin.status_code == 403
    _assert_error(no_origin.json(), "origin_rejected")
    foreign = await api.client.post(
        "/v1/projects", json=payload, headers={"Origin": "http://evil.example"}
    )
    assert foreign.status_code == 403
    allowed = await api.client.post("/v1/projects", json=payload, headers=origin)
    assert allowed.status_code == 201

    logout = await api.client.delete("/v1/auth/session")
    assert logout.status_code == 204
    assert (await api.client.get("/v1/auth/me")).status_code == 401


async def test_login_requires_allowed_origin(api: ApiHarness) -> None:
    response = await api.client.post("/v1/auth/session", json={"token": api.token})
    assert response.status_code == 403


async def test_failed_logins_are_throttled(api: ApiHarness) -> None:
    origin = {"Origin": WEB_ORIGIN}
    for _ in range(10):
        response = await api.client.post("/v1/auth/session", json={"token": "x"}, headers=origin)
        assert response.status_code == 401
    blocked = await api.client.post("/v1/auth/session", json={"token": api.token}, headers=origin)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


async def test_tampered_and_expired_sessions_are_rejected(api: ApiHarness) -> None:
    provider = LocalTokenProvider(api.token, session_ttl_seconds=300)
    issued = provider.issue_session()
    version, payload, signature = issued.cookie_value.split(".")
    forged = f"{version}.{payload}.{'A' * len(signature)}"
    api.client.cookies.set(SESSION_COOKIE, forged)
    assert (await api.client.get("/v1/auth/me")).status_code == 401

    old = provider.issue_session(now=time.time() - 3600)
    assert provider.verify_session(old.cookie_value) is None
    other = LocalTokenProvider("another-token-" + "x" * 40, session_ttl_seconds=300)
    assert other.verify_session(issued.cookie_value) is None


async def test_non_loopback_clients_are_refused(api_factory: ApiFactory) -> None:
    harness = await api_factory(client_addr=("10.1.2.3", 40000))
    response = await harness.client.get("/v1/health/live")
    assert response.status_code == 403
    _assert_error(response.json(), "non_loopback_client")


async def test_dns_rebinding_host_headers_are_refused(api_factory: ApiFactory) -> None:
    harness = await api_factory(base_url="http://attacker.example:8710")
    response = await harness.client.get("/v1/health/live")
    assert response.status_code == 403
    _assert_error(response.json(), "host_not_allowed")


async def test_validation_errors_are_structured_without_echoing_input(api: ApiHarness) -> None:
    response = await api.client.post(
        "/v1/projects",
        json={"workspace_id": "not-a-uuid", "name": "x", "secret_field": "s3cr3t-value"},
        headers=api.auth,
    )
    assert response.status_code == 400
    body = response.json()
    _assert_error(body, "validation_failed")
    assert "s3cr3t-value" not in response.text


async def test_capabilities_disclose_planned_features(api: ApiHarness) -> None:
    response = await api.client.get("/v1/capabilities", headers=api.auth)
    assert response.status_code == 200
    states = {c["id"]: c["state"] for c in response.json()["capabilities"]}
    assert states["readiness"] == "available"
    assert states["zip_intake"] == "available"
    assert states["baseline_analysis"] == "available"
    assert states["architecture_graph"] == "available"
    assert states["issue_lifecycle"] == states["exports"] == "available"
    assert states["ai_investigation"] == "not_configured"  # no model provider in tests
    assert states["fix_workbench"] == states["git_integration"] == "planned"
