"""Demo account (isolation, origin checks, quotas) and the built-in sample project."""

from __future__ import annotations

import io
import zipfile
from uuid import UUID

import pytest

from crp_api.auth.local_token import LocalTokenProvider
from crp_api.services.samples import SAMPLE_NAME, sample_archive
from crp_core.db.identity import DEMO_SUBJECT, LOCAL_SUBJECT
from crp_core.workflows.contracts import DiagnosticWorkflowInput
from crp_core.workflows.gateway import DiagnosticRun, WorkerPollerStatus, WorkflowServiceStatus

from .conftest import WEB_ORIGIN, ApiFactory, ApiHarness

ORIGIN = {"Origin": WEB_ORIGIN}


class RecordingGateway:
    """Test double: accepts workflow starts and records them (no execution)."""

    def __init__(self) -> None:
        self.intakes: list[UUID] = []
        self.ai_runs: list[UUID] = []
        self.cancelled_ai_runs: list[UUID] = []
        self.fix_validations: list[UUID] = []
        self.cancelled_fix_validations: list[UUID] = []
        self.git_reviews: list[UUID] = []
        self.cancelled_git_reviews: list[UUID] = []
        self.change_set_checks: list[UUID] = []
        self.cancelled_change_set_checks: list[UUID] = []

    async def describe_service(self) -> WorkflowServiceStatus:
        return WorkflowServiceStatus(address="test", namespace="test", server_version=None)

    async def describe_workers(self) -> WorkerPollerStatus:
        return WorkerPollerStatus(
            task_queue="test", workflow_pollers=1, activity_pollers=1, newest_poll_age_seconds=0
        )

    async def start_diagnostic(self, payload: DiagnosticWorkflowInput) -> str:
        return "unused"

    async def get_diagnostic(self, workflow_id: str) -> DiagnosticRun:
        raise AssertionError("not used")

    async def start_intake(self, intake_id: UUID) -> str:
        self.intakes.append(intake_id)
        return f"crp-intake-{intake_id.hex}"

    async def start_scan(self, scan_id: UUID) -> str:
        return f"crp-scan-{scan_id.hex}"

    async def cancel_scan(self, scan_id: UUID) -> None:
        return None

    async def start_ai_run(self, run_id: UUID) -> str:
        self.ai_runs.append(run_id)
        return f"crp-ai-{run_id.hex}"

    async def cancel_ai_run(self, run_id: UUID) -> None:
        self.cancelled_ai_runs.append(run_id)

    async def start_fix_validation(self, validation_id: UUID) -> str:
        self.fix_validations.append(validation_id)
        return f"crp-fix-{validation_id.hex}"

    async def cancel_fix_validation(self, validation_id: UUID) -> None:
        self.cancelled_fix_validations.append(validation_id)

    async def start_git_review(self, review_id: UUID) -> str:
        self.git_reviews.append(review_id)
        return f"crp-review-{review_id.hex}"

    async def cancel_git_review(self, review_id: UUID) -> None:
        self.cancelled_git_reviews.append(review_id)

    async def start_change_set_check(self, check_id: UUID) -> str:
        self.change_set_checks.append(check_id)
        return f"crp-cscheck-{check_id.hex}"

    async def cancel_change_set_check(self, check_id: UUID) -> None:
        self.cancelled_change_set_checks.append(check_id)

    async def close(self) -> None:
        return None


def _local_workspace(api: ApiHarness) -> str:
    assert api.identity is not None
    return str(api.identity.workspace_id)


def test_sample_archive_is_deterministic_and_complete() -> None:
    first, second = sample_archive(), sample_archive()
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        names = set(archive.namelist())
        config = archive.read("online-store/web/src/config.js").decode()
    assert {
        "online-store/pom.xml",
        "online-store/web/package-lock.json",
        "online-store/src/main/java/com/example/store/service/PaymentService.java",
        "online-store/web/src/checkout.ts",
    } <= names
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    assert "ghp_" in config and "never valid" in config  # generated, not stored in the repo


def test_demo_sessions_require_the_demo_to_stay_enabled() -> None:
    token = "t" * 43
    enabled = LocalTokenProvider(token, 3600, demo_enabled=True)
    disabled = LocalTokenProvider(token, 3600)
    cookie = enabled.issue_session(subject=DEMO_SUBJECT).cookie_value
    assert enabled.verify_session(cookie) == DEMO_SUBJECT
    assert disabled.verify_session(cookie) is None
    assert disabled.verify_session(disabled.issue_session().cookie_value) == LOCAL_SUBJECT
    with pytest.raises(ValueError, match="enabled subjects"):
        disabled.issue_session(subject=DEMO_SUBJECT)


@pytest.mark.integration
async def test_sign_in_options_are_public(api_factory: ApiFactory) -> None:
    default = await api_factory()
    options = await default.client.get("/v1/auth/options")
    assert options.status_code == 200
    assert options.json() == {"environment": "test", "demo_enabled": False}
    refused = await default.client.post("/v1/auth/demo-session", headers=ORIGIN)
    assert refused.status_code == 404
    assert refused.json()["code"] == "demo_disabled"

    demo = await api_factory(demo_enabled=True)
    assert (await demo.client.get("/v1/auth/options")).json()["demo_enabled"] is True
    no_origin = await demo.client.post("/v1/auth/demo-session")
    assert no_origin.status_code == 403
    assert no_origin.json()["code"] == "origin_rejected"


@pytest.mark.integration
async def test_demo_account_cannot_reach_the_owner_workspace(api_factory: ApiFactory) -> None:
    api = await api_factory(demo_enabled=True)
    owned = await api.client.post(
        "/v1/projects",
        json={"workspace_id": _local_workspace(api), "name": "Owner secrets"},
        headers=api.auth,
    )
    assert owned.status_code == 201
    owner_project = owned.json()["id"]

    signed_in = await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    assert signed_in.status_code == 200
    assert signed_in.json()["subject"] == DEMO_SUBJECT
    me = (await api.client.get("/v1/auth/me")).json()  # cookie session from here on
    assert me["is_demo"] is True and me["is_operator"] is False
    assert [(w["slug"], w["role"]) for w in me["workspaces"]] == [("demo", "member")]
    demo_workspace = me["workspaces"][0]["workspace_id"]

    assert (await api.client.get("/v1/projects")).json()["items"] == []
    assert (await api.client.get(f"/v1/projects/{owner_project}")).status_code == 404
    foreign = await api.client.post(
        "/v1/projects",
        json={"workspace_id": _local_workspace(api), "name": "Intrusion"},
        headers=ORIGIN,
    )
    assert foreign.status_code == 404
    assert (
        await api.client.post("/v1/diagnostics/workflow-runs", headers=ORIGIN)
    ).status_code == 403
    mine = await api.client.post(
        "/v1/projects", json={"workspace_id": demo_workspace, "name": "Demo work"}, headers=ORIGIN
    )
    assert mine.status_code == 201

    owner_view = await api.client.get("/v1/projects", headers=api.auth)
    assert [p["name"] for p in owner_view.json()["items"]] == ["Owner secrets"]


@pytest.mark.integration
async def test_demo_project_quota(api_factory: ApiFactory) -> None:
    api = await api_factory(demo_enabled=True, demo_max_projects=2)
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    workspace = (await api.client.get("/v1/auth/me")).json()["workspaces"][0]["workspace_id"]
    for name in ("One", "Two"):
        created = await api.client.post(
            "/v1/projects", json={"workspace_id": workspace, "name": name}, headers=ORIGIN
        )
        assert created.status_code == 201
    third = await api.client.post(
        "/v1/projects", json={"workspace_id": workspace, "name": "Three"}, headers=ORIGIN
    )
    assert third.status_code == 429
    assert third.json()["code"] == "demo_limit_reached"
    sample = await api.client.post(
        "/v1/projects/sample", json={"workspace_id": workspace}, headers=ORIGIN
    )
    assert sample.status_code == 429
    # The owner is not subject to demo quotas.
    for name in ("A", "B", "C"):
        owner = await api.client.post(
            "/v1/projects",
            json={"workspace_id": _local_workspace(api), "name": name},
            headers=api.auth,
        )
        assert owner.status_code == 201


@pytest.mark.integration
async def test_sample_project_is_created_through_the_intake_path(api_factory: ApiFactory) -> None:
    gateway = RecordingGateway()
    api = await api_factory(gateway=gateway)
    body = {"workspace_id": _local_workspace(api)}
    first = await api.client.post("/v1/projects/sample", json=body, headers=api.auth)
    assert first.status_code == 202, first.text
    created = first.json()
    assert created["project"]["name"] == SAMPLE_NAME
    assert created["project"]["origin"] == "synthetic_fixture"
    assert created["intake"]["state"] == "VALIDATING"
    assert created["intake"]["archive_bytes"] == len(sample_archive())
    assert gateway.intakes == [UUID(created["intake"]["id"])]
    intake = await api.client.get(f"/v1/intakes/{created['intake']['id']}", headers=api.auth)
    assert intake.json()["project_id"] == created["project"]["id"]

    second = await api.client.post("/v1/projects/sample", json=body, headers=api.auth)
    assert second.status_code == 202
    assert second.json()["project"]["name"] == f"{SAMPLE_NAME} (2)"


@pytest.mark.integration
async def test_sample_project_reports_an_unavailable_workflow_service(api: ApiHarness) -> None:
    response = await api.client.post(
        "/v1/projects/sample", json={"workspace_id": _local_workspace(api)}, headers=api.auth
    )
    assert response.status_code == 503
    error = response.json()
    assert error["code"] == "workflow_unavailable"
    assert {"project_id", "intake_id"} <= set(error["details"])
