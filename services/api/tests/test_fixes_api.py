"""Fix proposals through the API (P05): options, preparation, edits under the policy, rejection,
validation requests, budgets, exports and moving a fix to another upload. The validation ladder
itself runs in the worker tests; here the workflow service is a recording test double."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import select, update

from crp_analysis.fixes.export import fix_export_schema
from crp_analysis.manifest import blob_key
from crp_core.artifacts import ArtifactKey, create_artifact_store
from crp_core.db.models import (
    EngineRun,
    FileEntry,
    Finding,
    FixProposal,
    FixValidation,
    Project,
    Scan,
    Snapshot,
    Source,
)
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_core.domain.states import CaptureStatus, FileDisposition, SourceMode

from .conftest import WEB_ORIGIN, ApiFactory, ApiHarness
from .test_demo_and_sample import RecordingGateway

pytestmark = pytest.mark.integration
ORIGIN = {"Origin": WEB_ORIGIN}
JAVA = "src/Order.java"
JAVA_TEXT = (
    'class Order {\n    boolean paid(String status) {\n        return status == "PAID";\n    }\n}\n'
)


async def _project(api: ApiHarness) -> str:
    assert api.identity is not None
    created = await api.client.post(
        "/v1/projects",
        json={
            "workspace_id": str(api.identity.workspace_id),
            "name": f"Fixes {uuid.uuid4().hex[:8]}",
        },
        headers=api.auth,
    )
    return str(created.json()["id"])


async def _upload(api: ApiHarness, project: str, files: dict[str, str]) -> dict[str, Any]:
    """A frozen upload with stored files (metadata and blobs only; nothing analysed)."""
    store = create_artifact_store(api.settings)
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            row = await session.get(Project, uuid.UUID(project))
            assert row is not None
            source = Source(
                workspace_id=row.workspace_id,
                project_id=row.id,
                mode=SourceMode.ZIP_UPLOAD.value,
                display_name="fix.zip",
            )
            session.add(source)
            await session.flush()
            snapshot = Snapshot(
                workspace_id=row.workspace_id,
                project_id=row.id,
                source_id=source.id,
                capture_status=CaptureStatus.FROZEN.value,
                manifest_sha256=hashlib.sha256(json.dumps(files).encode()).hexdigest(),
                frozen_at=datetime.now(UTC),
                file_count=len(files),
            )
            session.add(snapshot)
            await session.flush()
            entries = {}
            for path, text in files.items():
                data = text.encode()
                sha = hashlib.sha256(data).hexdigest()
                store.put_bytes(ArtifactKey(blob_key(sha)), data, overwrite=True)
                entry = FileEntry(
                    workspace_id=row.workspace_id,
                    project_id=row.id,
                    snapshot_id=snapshot.id,
                    path=path,
                    disposition=FileDisposition.ANALYZABLE.value,
                    blob_sha256=sha,
                    size_bytes=len(data),
                    language="java" if path.endswith(".java") else None,
                )
                session.add(entry)
                entries[path] = entry
            await session.flush()
            return {
                "workspace": row.workspace_id,
                "project": row.id,
                "snapshot": snapshot.id,
                "entries": {p: e.id for p, e in entries.items()},
            }
    finally:
        await engine.dispose()


async def _finding(
    api: ApiHarness, upload: dict[str, Any], path: str, engine_name: str, rule: str, line: int
) -> str:
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            scan = Scan(
                workspace_id=upload["workspace"],
                project_id=upload["project"],
                snapshot_id=upload["snapshot"],
                mode="baseline",
                policy_version="crp-baseline-v1",
                idempotency_key=uuid.uuid4().hex,
                state="SUCCEEDED",
            )
            session.add(scan)
            await session.flush()
            run = EngineRun(scan_id=scan.id, engine=engine_name, state="SUCCEEDED")
            session.add(run)
            await session.flush()
            finding = Finding(
                workspace_id=upload["workspace"],
                project_id=upload["project"],
                snapshot_id=upload["snapshot"],
                scan_id=scan.id,
                engine_run_id=run.id,
                file_entry_id=upload["entries"][path],
                fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                correlation_key=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                engine=engine_name,
                engine_version="7.27.0",
                rule_id=rule,
                severity="medium",
                category="correctness",
                confidence="high",
                title="Strings compared with ==",
                message="Use equals() to compare strings",
                anchor_kind="source_span",
                start_line=line,
                end_line=line,
                status="OPEN",
            )
            session.add(finding)
            await session.flush()
            return str(finding.id)
    finally:
        await engine.dispose()


def _git_apply_check(tree: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "apply", "--check", "-p1", "fix.diff"],  # noqa: S607
        cwd=tree,
        capture_output=True,
        check=False,
    )


async def _prepared(api: ApiHarness) -> tuple[str, str, dict[str, Any]]:
    project = await _project(api)
    upload = await _upload(api, project, {JAVA: JAVA_TEXT})
    finding = await _finding(api, upload, JAVA, "pmd", "UseEqualsToCompareStrings", 3)
    return project, finding, upload


async def test_prepare_edit_reject_and_export(api_factory: ApiFactory, tmp_path: Path) -> None:
    api = await api_factory(gateway=RecordingGateway())
    project, finding, _ = await _prepared(api)
    options = (await api.client.get(f"/v1/findings/{finding}/fix-options", headers=api.auth)).json()
    assert options["options"] == [
        {
            "recipe_id": "java:string-literal-equals",
            "title": "Compare the text with equals()",
            "available": True,
            "reason": None,
        }
    ]
    created = await api.client.post(
        f"/v1/findings/{finding}/fix-proposals",
        json={"recipe_id": "java:string-literal-equals"},
        headers=api.auth,
    )
    assert created.status_code == 201, created.text
    fix = created.json()
    assert fix["state"] == "PROPOSED" and fix["path"] == JAVA and fix["changed_lines"] == 1
    assert fix["edits"][0]["replacement"] == ['        return "PAID".equals(status);']
    assert "Not validated yet for this exact patch." in fix["labels"]
    again = await api.client.post(
        f"/v1/findings/{finding}/fix-proposals",
        json={"recipe_id": "java:string-literal-equals"},
        headers=api.auth,
    )
    assert again.json()["id"] == fix["id"]  # the same patch is not proposed twice

    # The downloaded patch applies to exactly this upload, and not to a changed copy.
    patch = await api.client.get(f"/v1/fix-proposals/{fix['id']}/patch", headers=api.auth)
    assert patch.headers["content-type"].startswith("text/x-diff")
    assert fix["patch_sha256"] in patch.text
    for name, content, ok in [
        ("exact", JAVA_TEXT, True),
        ("changed", JAVA_TEXT.replace('"PAID"', '"SETTLED"'), False),
    ]:
        tree = tmp_path / name
        (tree / "src").mkdir(parents=True)
        (tree / JAVA).write_text(content)
        (tree / "fix.diff").write_text(patch.text)
        result = _git_apply_check(tree)
        assert (result.returncode == 0) is ok, (name, result.stderr)

    summary = (
        await api.client.get(f"/v1/fix-proposals/{fix['id']}/summary", headers=api.auth)
    ).json()
    assert list(Draft202012Validator(fix_export_schema()).iter_errors(summary)) == []
    assert summary["applies_to"]["base_sha256"] == fix["base_sha256"]
    assert any("Not compiled" in risk for risk in summary["known_risks"])

    # Edits: a suppression is refused, a real change resets the proposal and marks it edited.
    edit_url = f"/v1/fix-proposals/{fix['id']}/edits"
    silenced = await api.client.put(
        edit_url,
        json={
            "version": fix["version"],
            "edits": [
                {"start_line": 3, "replacement": ['        return status == "PAID"; // NOPMD']}
            ],
        },
        headers=api.auth,
    )
    assert silenced.status_code == 422 and silenced.json()["code"] == "fix_not_allowed"
    assert "suppression_added" in silenced.json()["details"]["violations"]
    stale = await api.client.put(
        edit_url,
        json={"version": fix["version"] + 3, "edits": [{"start_line": 3, "replacement": ["x"]}]},
        headers=api.auth,
    )
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    unknown = await api.client.put(
        edit_url,
        json={"version": fix["version"], "edits": [{"start_line": 1, "replacement": ["x"]}]},
        headers=api.auth,
    )
    assert unknown.status_code == 422 and unknown.json()["code"] == "unknown_edit"
    edited = await api.client.put(
        edit_url,
        json={
            "version": fix["version"],
            "edits": [
                {
                    "start_line": 3,
                    "replacement": ['        return "PAID".equalsIgnoreCase(status);'],
                }
            ],
        },
        headers=api.auth,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["edited"] is True and edited.json()["patch_sha256"] != fix["patch_sha256"]

    rejected = await api.client.post(
        f"/v1/fix-proposals/{fix['id']}/reject",
        json={"reason": "Handled by a refactor"},
        headers=api.auth,
    )
    assert rejected.json()["state"] == "REJECTED"
    blocked = await api.client.post(f"/v1/fix-proposals/{fix['id']}/validations", headers=api.auth)
    assert blocked.status_code == 409 and blocked.json()["code"] == "fix_rejected"
    listed = (
        await api.client.get(f"/v1/projects/{project}/fix-proposals", headers=api.auth)
    ).json()
    assert [item["id"] for item in listed["items"]] == [fix["id"]]


async def test_no_fix_when_the_code_does_not_match(api_factory: ApiFactory) -> None:
    api = await api_factory(gateway=RecordingGateway())
    project = await _project(api)
    text = "class A {\n  boolean b(String x, String y) { return x == y; }\n}\n"
    upload = await _upload(api, project, {"src/A.java": text})
    finding = await _finding(api, upload, "src/A.java", "pmd", "UseEqualsToCompareStrings", 2)
    options = (await api.client.get(f"/v1/findings/{finding}/fix-options", headers=api.auth)).json()
    [option] = options["options"]
    assert option["available"] is False and "string literal" in option["reason"]
    refused = await api.client.post(
        f"/v1/findings/{finding}/fix-proposals",
        json={"recipe_id": "java:string-literal-equals"},
        headers=api.auth,
    )
    assert refused.status_code == 409 and refused.json()["code"] == "no_fix"
    other = await api.client.post(
        f"/v1/findings/{finding}/fix-proposals",
        json={"recipe_id": "eslint:eqeqeq"},
        headers=api.auth,
    )
    assert other.status_code == 422 and other.json()["code"] == "unknown_recipe"


async def test_validation_requests_budget_and_service_outage(api_factory: ApiFactory) -> None:
    gateway = RecordingGateway()
    api = await api_factory(gateway=gateway)
    _, finding, _ = await _prepared(api)
    fix = (
        await api.client.post(
            f"/v1/findings/{finding}/fix-proposals",
            json={"recipe_id": "java:string-literal-equals"},
            headers=api.auth,
        )
    ).json()
    url = f"/v1/fix-proposals/{fix['id']}/validations"
    queued = await api.client.post(url, headers=api.auth)
    assert queued.status_code == 202 and queued.json()["state"] == "QUEUED"
    assert gateway.fix_validations == [uuid.UUID(queued.json()["id"])]
    busy = await api.client.post(url, headers=api.auth)
    assert busy.status_code == 409 and busy.json()["code"] == "validation_running"
    cancel = await api.client.post(
        f"/v1/fix-validations/{queued.json()['id']}/cancel", headers=api.auth
    )
    assert cancel.json()["cancel_requested_at"] is not None
    assert gateway.cancelled_fix_validations == [uuid.UUID(queued.json()["id"])]

    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            await session.execute(
                update(FixProposal)
                .where(FixProposal.id == uuid.UUID(fix["id"]))
                .values(validations_used=FixProposal.max_validations, state="PROPOSED")
            )
            await session.execute(update(FixValidation).values(state="CANCELED"))
    finally:
        await engine.dispose()
    exhausted = await api.client.post(url, headers=api.auth)
    assert (
        exhausted.status_code == 409 and exhausted.json()["code"] == "validation_budget_exhausted"
    )

    down = await api_factory()  # default gateway: workflow service unreachable
    _, other, _ = await _prepared(down)
    second = (
        await down.client.post(
            f"/v1/findings/{other}/fix-proposals",
            json={"recipe_id": "java:string-literal-equals"},
            headers=down.auth,
        )
    ).json()
    refused = await down.client.post(
        f"/v1/fix-proposals/{second['id']}/validations", headers=down.auth
    )
    assert refused.status_code == 503 and refused.json()["code"] == "workflow_unavailable"
    after = (await down.client.get(f"/v1/fix-proposals/{second['id']}", headers=down.auth)).json()
    assert after["state"] == "PROPOSED" and after["validations_used"] == 0  # not consumed
    assert after["latest_validation"]["error_code"] == "workflow_unavailable"


async def test_moving_a_fix_to_another_upload(api_factory: ApiFactory) -> None:
    api = await api_factory(gateway=RecordingGateway())
    project, finding, _ = await _prepared(api)
    fix = (
        await api.client.post(
            f"/v1/findings/{finding}/fix-proposals",
            json={"recipe_id": "java:string-literal-equals"},
            headers=api.auth,
        )
    ).json()
    shifted = await _upload(api, project, {JAVA: "// new header\n\n" + JAVA_TEXT})
    moved = await api.client.post(
        f"/v1/fix-proposals/{fix['id']}/rebase",
        json={"snapshot_id": str(shifted["snapshot"])},
        headers=api.auth,
    )
    assert moved.status_code == 201, moved.text
    assert moved.json()["edits"][0]["start_line"] == 5  # followed the two inserted lines
    assert moved.json()["state"] == "PROPOSED" and moved.json()["id"] != fix["id"]
    changed = await _upload(api, project, {JAVA: JAVA_TEXT.replace("status ==", "state ==")})
    conflict = await api.client.post(
        f"/v1/fix-proposals/{fix['id']}/rebase",
        json={"snapshot_id": str(changed["snapshot"])},
        headers=api.auth,
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "patch_conflict"
    missing = await _upload(api, project, {"src/Other.java": "class Other {}\n"})
    gone = await api.client.post(
        f"/v1/fix-proposals/{fix['id']}/rebase",
        json={"snapshot_id": str(missing["snapshot"])},
        headers=api.auth,
    )
    assert gone.status_code == 409


async def test_fixes_of_other_workspaces_are_invisible(api_factory: ApiFactory) -> None:
    api = await api_factory(gateway=RecordingGateway(), demo_enabled=True)
    _, finding, _ = await _prepared(api)
    fix = (
        await api.client.post(
            f"/v1/findings/{finding}/fix-proposals",
            json={"recipe_id": "java:string-literal-equals"},
            headers=api.auth,
        )
    ).json()
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    for method, path in [
        ("GET", f"/v1/findings/{finding}/fix-options"),
        ("GET", f"/v1/fix-proposals/{fix['id']}"),
        ("GET", f"/v1/fix-proposals/{fix['id']}/patch"),
        ("POST", f"/v1/fix-proposals/{fix['id']}/validations"),
        ("POST", f"/v1/fix-proposals/{fix['id']}/reject"),
    ]:
        response = await api.client.request(
            method, path, headers=ORIGIN, json={"reason": "not mine"}
        )
        assert response.status_code == 404, (method, path, response.text)
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            stored = await session.scalar(
                select(FixProposal.state).where(FixProposal.id == uuid.UUID(fix["id"]))
            )
    finally:
        await engine.dispose()
    assert stored == "PROPOSED"
