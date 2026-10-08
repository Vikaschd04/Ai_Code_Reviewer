"""Fix workspaces through the API (P08): editing many files, policy flags, conflicts, bulk recipe
fixes, the issue queue, checks, exports that apply only to the exact upload, comparing uploads,
isolation and deletion. The check workflow itself runs in the worker tests; here the workflow
service is a recording test double."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from crp_analysis.fixes.export import change_set_export_schema
from crp_analysis.manifest import blob_key
from crp_core.artifacts import ArtifactKey, create_artifact_store
from crp_core.db.models import (
    ChangeSetCheck,
    EngineRun,
    FileEntry,
    Finding,
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
    "class Order {\n"
    "    boolean paid(String status) {\n"
    '        return status == "PAID";\n'
    "    }\n"
    "    boolean open(String status) {\n"
    '        return status == "OPEN";\n'
    "    }\n"
    "}\n"
)
WIN = "src/win.js"
WIN_TEXT = "const a = 1;\r\nconsole.log(a)\r\n"
GONE = "src/remove.js"
LOGO = "assets/logo.png"
FILES = {
    JAVA: JAVA_TEXT,
    WIN: WIN_TEXT,
    GONE: "export const old = 1;\n",
    "src/keep.js": "export const keep = true;\n",
}


async def _project(api: ApiHarness) -> str:
    assert api.identity is not None
    created = await api.client.post(
        "/v1/projects",
        json={
            "workspace_id": str(api.identity.workspace_id),
            "name": f"Workspace {uuid.uuid4().hex[:8]}",
        },
        headers=api.auth,
    )
    return str(created.json()["id"])


async def _upload(
    api: ApiHarness, project: str, files: dict[str, str | bytes], *, binary: bool = True
) -> dict[str, Any]:
    """A frozen upload with stored text files and one binary file that is not stored."""
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
                display_name="shop.zip",
            )
            session.add(source)
            await session.flush()
            snapshot = Snapshot(
                workspace_id=row.workspace_id,
                project_id=row.id,
                source_id=source.id,
                capture_status=CaptureStatus.FROZEN.value,
                manifest_sha256=hashlib.sha256(repr(sorted(files.items())).encode()).hexdigest(),
                frozen_at=datetime.now(UTC),
                file_count=len(files),
            )
            session.add(snapshot)
            await session.flush()
            entries = {}
            for path, text in files.items():
                data = text if isinstance(text, bytes) else text.encode()
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
                    language="java" if path.endswith(".java") else "javascript",
                )
                session.add(entry)
                entries[path] = entry
            if binary:
                session.add(
                    FileEntry(
                        workspace_id=row.workspace_id,
                        project_id=row.id,
                        snapshot_id=snapshot.id,
                        path=LOGO,
                        disposition=FileDisposition.BINARY.value,
                        reason="binary",
                        size_bytes=120,
                    )
                )
            await session.flush()
            return {
                "workspace": row.workspace_id,
                "project": row.id,
                "snapshot": snapshot.id,
                "entries": {p: e.id for p, e in entries.items()},
            }
    finally:
        await engine.dispose()


async def _review(
    api: ApiHarness, upload: dict[str, Any], findings: list[tuple[str, str, str, int]]
) -> list[str]:
    """One finished baseline review of the upload with the given (path, engine, rule, line)."""
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
            runs: dict[str, EngineRun] = {}
            ids = []
            for path, engine_name, rule, line in findings:
                if engine_name not in runs:
                    runs[engine_name] = EngineRun(
                        scan_id=scan.id, engine=engine_name, state="SUCCEEDED"
                    )
                    session.add(runs[engine_name])
                    await session.flush()
                finding = Finding(
                    workspace_id=upload["workspace"],
                    project_id=upload["project"],
                    snapshot_id=upload["snapshot"],
                    scan_id=scan.id,
                    engine_run_id=runs[engine_name].id,
                    file_entry_id=upload["entries"][path],
                    fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                    correlation_key=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                    engine=engine_name,
                    engine_version="1",
                    rule_id=rule,
                    severity="high" if engine_name == "pmd" else "medium",
                    category="correctness",
                    confidence="high",
                    title=f"{rule} problem",
                    message="Fix it",
                    anchor_kind="source_span",
                    start_line=line,
                    end_line=line,
                    status="OPEN",
                )
                session.add(finding)
                await session.flush()
                ids.append(str(finding.id))
            return ids
    finally:
        await engine.dispose()


async def _workspace(api: ApiHarness, project: str, **body: Any) -> dict[str, Any]:
    created = await api.client.post(
        f"/v1/projects/{project}/change-sets", json=body, headers=api.auth
    )
    assert created.status_code == 201, created.text
    result: dict[str, Any] = created.json()
    return result


async def _save(
    api: ApiHarness, ws: dict[str, Any], path: str, content: str, **extra: Any
) -> dict[str, Any]:
    response = await api.client.put(
        f"/v1/change-sets/{ws['id']}/file",
        json={"version": ws["version"], "path": path, "content": content, **extra},
        headers=api.auth,
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _tree(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())


def _git_apply(tree: Path, patch: Path, *, check_only: bool) -> subprocess.CompletedProcess[bytes]:
    args = ["git", "apply", "-p1", str(patch)]
    if check_only:
        args.insert(2, "--check")
    return subprocess.run(args, cwd=tree, capture_output=True, check=False)  # noqa: S603


async def _blob_count(api: ApiHarness, snapshot: uuid.UUID) -> list[tuple[str, str | None]]:
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            rows = await session.execute(
                select(FileEntry.path, FileEntry.blob_sha256)
                .where(FileEntry.snapshot_id == snapshot)
                .order_by(FileEntry.path)
            )
            return [(p, s) for p, s in rows.all()]
    finally:
        await engine.dispose()


async def test_edit_files_with_flags_conflicts_and_line_endings(api: ApiHarness) -> None:
    project = await _project(api)
    upload = await _upload(api, project, FILES)
    before = await _blob_count(api, upload["snapshot"])
    ws = await _workspace(api, project)
    assert ws["base_snapshot_id"] == str(upload["snapshot"])
    assert ws["base_scan_id"] is None and ws["files"] == [] and ws["version"] == 1
    assert ws["base_name"] == "shop.zip" and ws["can_edit"] is True
    listed = (await api.client.get(f"/v1/projects/{project}/change-sets", headers=api.auth)).json()
    assert [i["id"] for i in listed["items"]] == [ws["id"]]

    file = (
        await api.client.get(
            f"/v1/change-sets/{ws['id']}/file", params={"path": JAVA}, headers=api.auth
        )
    ).json()
    assert file["content"] == JAVA_TEXT == file["base_content"] and file["action"] is None
    assert file["editable"] is True and file["line_ending"] == "lf"

    edited = JAVA_TEXT.replace('status == "PAID"', '"PAID".equals(status)')
    saved = await _save(api, ws, JAVA, edited)
    assert saved["flags"] == []
    ws = saved["change_set"]
    assert ws["version"] == 2
    assert [(f["path"], f["action"], f["sources"]) for f in ws["files"]] == [
        (JAVA, "modify", ["manual"])
    ]
    stale = await api.client.put(
        f"/v1/change-sets/{ws['id']}/file",
        json={"version": 1, "path": JAVA, "content": "x\n"},
        headers=api.auth,
    )
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"

    # Browser edits arrive with LF; the file keeps its CRLF line endings.
    ws = (await _save(api, ws, WIN, "const a = 1;\nconsole.log(a);\n"))["change_set"]
    win = (
        await api.client.get(
            f"/v1/change-sets/{ws['id']}/file", params={"path": WIN}, headers=api.auth
        )
    ).json()
    assert win["content"] == "const a = 1;\r\nconsole.log(a);\r\n" and win["line_ending"] == "crlf"

    # Adding, deleting, refused paths and files that cannot be edited.
    ws = (await _save(api, ws, "src/Added.java", "class Added {}\n"))["change_set"]
    deleted = await api.client.post(
        f"/v1/change-sets/{ws['id']}/file/delete",
        json={"version": ws["version"], "path": GONE},
        headers=api.auth,
    )
    assert deleted.status_code == 200, deleted.text
    ws = deleted.json()
    actions = {f["path"]: f["action"] for f in ws["files"]}
    assert actions == {JAVA: "modify", WIN: "modify", "src/Added.java": "add", GONE: "delete"}
    for bad in ("../escape.js", ".git/config", "/etc/passwd", "src/../../x"):
        refused = await api.client.put(
            f"/v1/change-sets/{ws['id']}/file",
            json={"version": ws["version"], "path": bad, "content": "x\n"},
            headers=api.auth,
        )
        assert refused.status_code == 422, (bad, refused.text)
    binary = await api.client.put(
        f"/v1/change-sets/{ws['id']}/file",
        json={"version": ws["version"], "path": LOGO, "content": "x"},
        headers=api.auth,
    )
    assert binary.status_code == 409 and binary.json()["code"] == "not_editable"
    logo = (
        await api.client.get(
            f"/v1/change-sets/{ws['id']}/file", params={"path": LOGO}, headers=api.auth
        )
    ).json()
    assert logo["editable"] is False and "Binary" in logo["reason"] and logo["content"] is None

    # Manual edits are never refused for honesty flags, but they are reported and kept.
    silenced = await _save(api, ws, "src/keep.js", "// eslint-disable\nexport const keep = 1;\n")
    assert silenced["flags"] == ["suppression_added"]
    ws = silenced["change_set"]
    assert next(f for f in ws["files"] if f["path"] == "src/keep.js")["flags"] == [
        "suppression_added"
    ]
    # Saving the uploaded content again drops the file from the workspace.
    ws = (await _save(api, ws, "src/keep.js", FILES["src/keep.js"]))["change_set"]
    assert "src/keep.js" not in {f["path"] for f in ws["files"]}
    reverted = await api.client.post(
        f"/v1/change-sets/{ws['id']}/file/revert",
        json={"version": ws["version"], "path": "src/Added.java"},
        headers=api.auth,
    )
    ws = reverted.json()
    assert "src/Added.java" not in {f["path"] for f in ws["files"]}
    assert {e["source"] for e in ws["events"]} >= {"manual", "revert"}
    # The upload itself never changes.
    assert await _blob_count(api, upload["snapshot"]) == before
    store = create_artifact_store(api.settings)
    for path, sha in before:
        if sha is not None:
            data = store.read_bytes(ArtifactKey(blob_key(sha)), max_bytes=1 << 20)
            assert data.decode() == FILES[path]


async def test_exports_apply_only_to_the_exact_upload(api: ApiHarness, tmp_path: Path) -> None:
    project = await _project(api)
    upload = await _upload(api, project, FILES)
    ws = await _workspace(api, project, title="Release fixes")
    empty = await api.client.get(
        f"/v1/change-sets/{ws['id']}/export", params={"format": "patch"}, headers=api.auth
    )
    assert empty.status_code == 422 and empty.json()["code"] == "nothing_to_export"
    edited = JAVA_TEXT.replace('status == "PAID"', '"PAID".equals(status)')
    ws = (await _save(api, ws, JAVA, edited))["change_set"]
    ws = (await _save(api, ws, WIN, "const a = 1;\nconsole.log(a);\n"))["change_set"]
    ws = (await _save(api, ws, "src/new dir/Added.java", "class Added {}\n"))["change_set"]
    ws = (
        await api.client.post(
            f"/v1/change-sets/{ws['id']}/file/delete",
            json={"version": ws["version"], "path": GONE},
            headers=api.auth,
        )
    ).json()
    expected = {
        **FILES,
        JAVA: edited,
        WIN: "const a = 1;\r\nconsole.log(a);\r\n",
        "src/new dir/Added.java": "class Added {}\n",
    }
    expected.pop(GONE)

    patch = await api.client.get(
        f"/v1/change-sets/{ws['id']}/export", params={"format": "patch"}, headers=api.auth
    )
    assert patch.status_code == 200 and patch.headers["content-type"].startswith("text/x-diff")
    assert ws["content_sha256"][:8] in patch.headers["content-disposition"]
    assert str(upload["snapshot"]) in patch.text
    patch_file = tmp_path / "workspace.diff"
    patch_file.write_bytes(patch.content)
    exact = tmp_path / "exact"
    _tree(exact, FILES)
    assert _git_apply(exact, patch_file, check_only=True).returncode == 0
    assert _git_apply(exact, patch_file, check_only=False).returncode == 0
    for path, text in expected.items():
        assert (exact / path).read_bytes() == text.encode(), path
    assert not (exact / GONE).exists()
    drifted = tmp_path / "drifted"
    _tree(drifted, {**FILES, JAVA: JAVA_TEXT.replace("PAID", "SETTLED")})
    assert _git_apply(drifted, patch_file, check_only=True).returncode != 0

    changed = await api.client.get(
        f"/v1/change-sets/{ws['id']}/export", params={"format": "changed"}, headers=api.auth
    )
    assert changed.status_code == 200 and changed.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(changed.content)) as archive:
        names = sorted(archive.namelist())
        assert names == sorted(
            [
                JAVA,
                WIN,
                "src/new dir/Added.java",
                "refactorx-changes.json",
                "refactorx-changes.diff",
            ]
        )
        assert archive.read(WIN) == expected[WIN].encode()
        manifest = json.loads(archive.read("refactorx-changes.json"))
    assert manifest["deleted"] == [GONE]
    assert list(Draft202012Validator(change_set_export_schema()).iter_errors(manifest)) == []

    full = await api.client.get(
        f"/v1/change-sets/{ws['id']}/export", params={"format": "full"}, headers=api.auth
    )
    with zipfile.ZipFile(io.BytesIO(full.content)) as archive:
        names = set(archive.namelist())
        assert names == {*expected, "refactorx-changes.json", "refactorx-not-included.txt"}
        assert GONE not in names and LOGO not in names
        assert LOGO in archive.read("refactorx-not-included.txt").decode()
        for path, text in expected.items():
            assert archive.read(path) == text.encode(), path

    summary = (
        await api.client.get(
            f"/v1/change-sets/{ws['id']}/export", params={"format": "summary"}, headers=api.auth
        )
    ).json()
    assert list(Draft202012Validator(change_set_export_schema()).iter_errors(summary)) == []
    assert summary["workspace"]["content_sha256"] == ws["content_sha256"]
    assert summary["base"]["snapshot_id"] == str(upload["snapshot"])
    assert summary["check"]["checked"] is False
    assert "not compiled" in summary["not_verified"]
    markdown = await api.client.get(
        f"/v1/change-sets/{ws['id']}/export", params={"format": "summary-md"}, headers=api.auth
    )
    assert "# Release fixes" in markdown.text and "Not checked" in markdown.text
    events = (await api.client.get(f"/v1/change-sets/{ws['id']}", headers=api.auth)).json()
    assert sum(1 for e in events["events"] if e["source"] == "export") == 5


async def test_bulk_recipe_fixes_issue_queue_and_conflicts(api: ApiHarness) -> None:
    project = await _project(api)
    upload = await _upload(api, project, FILES)
    paid, opened, eval_id = await _review(
        api,
        upload,
        [
            (JAVA, "pmd", "UseEqualsToCompareStrings", 3),
            (JAVA, "pmd", "UseEqualsToCompareStrings", 6),
            ("src/keep.js", "eslint", "no-eval", 1),
        ],
    )
    ws = await _workspace(api, project)
    assert ws["base_scan_id"] is not None
    queue = (await api.client.get(f"/v1/change-sets/{ws['id']}/issues", headers=api.auth)).json()
    assert queue["total"] == 3
    assert queue["items"][0]["severity"] == "high"
    fixable = {i["finding_id"]: i["recipe_available"] for i in queue["items"]}
    assert fixable == {paid: True, opened: True, eval_id: False}
    only_fixable = (
        await api.client.get(
            f"/v1/change-sets/{ws['id']}/issues", params={"fixable": True}, headers=api.auth
        )
    ).json()
    assert only_fixable["total"] == 2

    # A hand edit on line 3 first: the recipe for line 3 now conflicts; line 6 still applies.
    hand = JAVA_TEXT.replace('status == "PAID"', 'java.util.Objects.equals(status, "PAID")')
    ws = (await _save(api, ws, JAVA, hand, finding_ids=[paid]))["change_set"]
    nothing = await api.client.post(
        f"/v1/change-sets/{ws['id']}/fixes", json={"version": ws["version"]}, headers=api.auth
    )
    assert nothing.status_code == 422
    stranger = str(uuid.uuid4())
    result = (
        await api.client.post(
            f"/v1/change-sets/{ws['id']}/fixes",
            json={"version": ws["version"], "finding_ids": [paid, opened, eval_id, stranger]},
            headers=api.auth,
        )
    ).json()
    assert [a["finding_id"] for a in result["applied"]] == [opened]
    reasons = {s["finding_id"]: s["reason"] for s in result["skipped"]}
    assert "changed in this workspace" in reasons[paid]
    assert "No automatic fix" in reasons[eval_id]
    assert "Not a finding" in reasons[stranger]
    ws = result["change_set"]
    text = (
        await api.client.get(
            f"/v1/change-sets/{ws['id']}/file", params={"path": JAVA}, headers=api.auth
        )
    ).json()["content"]
    assert 'java.util.Objects.equals(status, "PAID")' in text
    assert '"OPEN".equals(status)' in text
    java = next(f for f in ws["files"] if f["path"] == JAVA)
    assert java["sources"] == ["manual", "recipe"]

    # "All occurrences of a rule" in a fresh workspace fixes both lines.
    other = await _workspace(api, project)
    every = (
        await api.client.post(
            f"/v1/change-sets/{other['id']}/fixes",
            json={
                "version": other["version"],
                "engine": "pmd",
                "rule_id": "UseEqualsToCompareStrings",
            },
            headers=api.auth,
        )
    ).json()
    assert len(every["applied"]) == 2 and every["skipped"] == []
    text = (
        await api.client.get(
            f"/v1/change-sets/{other['id']}/file", params={"path": JAVA}, headers=api.auth
        )
    ).json()["content"]
    assert '"PAID".equals(status)' in text and '"OPEN".equals(status)' in text
    recipe_events = [e for e in every["change_set"]["events"] if e["source"] == "recipe"]
    assert {tuple(e["finding_ids"]) for e in recipe_events} == {(paid,), (opened,)}
    changed = (
        await api.client.get(f"/v1/change-sets/{other['id']}/issues", headers=api.auth)
    ).json()
    assert {i["finding_id"]: i["changed"] for i in changed["items"]}[paid] is True


async def test_checks_are_queued_once_and_bound_to_content(api_factory: ApiFactory) -> None:
    gateway = RecordingGateway()
    api = await api_factory(gateway=gateway)
    project = await _project(api)
    upload = await _upload(api, project, FILES)
    await _review(api, upload, [(JAVA, "pmd", "UseEqualsToCompareStrings", 3)])
    ws = await _workspace(api, project)
    empty = await api.client.post(f"/v1/change-sets/{ws['id']}/checks", headers=api.auth)
    assert empty.status_code == 422 and empty.json()["code"] == "nothing_to_check"
    ws = (await _save(api, ws, JAVA, JAVA_TEXT.replace("==", "!="), finding_ids=[]))["change_set"]
    started = await api.client.post(f"/v1/change-sets/{ws['id']}/checks", headers=api.auth)
    assert started.status_code == 202, started.text
    check = started.json()
    assert check["state"] == "QUEUED" and check["current"] is True
    assert check["content_sha256"] == ws["content_sha256"]
    assert gateway.change_set_checks == [uuid.UUID(check["id"])]
    again = await api.client.post(f"/v1/change-sets/{ws['id']}/checks", headers=api.auth)
    assert again.status_code == 409 and again.json()["code"] == "check_running"
    busy = await api.client.delete(f"/v1/change-sets/{ws['id']}", headers=api.auth)
    assert busy.status_code == 409
    # Editing after the check makes it no longer current.
    ws = (await _save(api, ws, WIN, "const a = 2;\n"))["change_set"]
    read = (await api.client.get(f"/v1/change-set-checks/{check['id']}", headers=api.auth)).json()
    assert read["current"] is False
    assert ws["latest_check"]["id"] == check["id"]
    cancelled = await api.client.post(
        f"/v1/change-set-checks/{check['id']}/cancel", headers=api.auth
    )
    assert cancelled.status_code == 200
    assert gateway.cancelled_change_set_checks == [uuid.UUID(check["id"])]
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            row = await session.get(ChangeSetCheck, uuid.UUID(check["id"]))
            assert row is not None and row.cancel_requested_at is not None
            assert [f["path"] for f in row.files] == [JAVA]
    finally:
        await engine.dispose()


async def test_check_fails_clearly_without_the_workflow_service(api: ApiHarness) -> None:
    project = await _project(api)
    await _upload(api, project, FILES)
    ws = await _workspace(api, project)
    ws = (await _save(api, ws, JAVA, "class Order {}\n"))["change_set"]
    refused = await api.client.post(f"/v1/change-sets/{ws['id']}/checks", headers=api.auth)
    assert refused.status_code == 503 and refused.json()["code"] == "workflow_unavailable"
    latest = (await api.client.get(f"/v1/change-sets/{ws['id']}", headers=api.auth)).json()
    assert latest["latest_check"]["state"] == "FAILED"
    assert latest["latest_check"]["error_code"] == "workflow_unavailable"


async def test_compare_two_uploads(api: ApiHarness) -> None:
    project = await _project(api)
    first = await _upload(api, project, FILES, binary=False)
    second = await _upload(
        api,
        project,
        {
            JAVA: JAVA_TEXT.replace("==", "!="),
            WIN: WIN_TEXT,
            "src/moved/keep.js": FILES["src/keep.js"],
            "src/brand.js": "export const brand = 1;\n",
        },
        binary=False,
    )
    compared = (
        await api.client.get(
            f"/v1/snapshots/{second['snapshot']}/compare",
            params={"base": str(first["snapshot"])},
            headers=api.auth,
        )
    ).json()
    assert compared["counts"] == {"added": 1, "modified": 1, "removed": 1, "renamed": 1}
    statuses = {(c["path"], c["status"], c["previous_path"]) for c in compared["changes"]}
    assert statuses == {
        ("src/brand.js", "added", None),
        (JAVA, "modified", None),
        (GONE, "removed", None),
        ("src/moved/keep.js", "renamed", "src/keep.js"),
    }
    one = (
        await api.client.get(
            f"/v1/snapshots/{second['snapshot']}/compare/file",
            params={"base": str(first["snapshot"]), "path": JAVA},
            headers=api.auth,
        )
    ).json()
    assert one["before"] == JAVA_TEXT and one["after"] == JAVA_TEXT.replace("==", "!=")
    added = (
        await api.client.get(
            f"/v1/snapshots/{second['snapshot']}/compare/file",
            params={"base": str(first["snapshot"]), "path": "src/brand.js"},
            headers=api.auth,
        )
    ).json()
    assert added["before"] is None and added["note"] == "New in this upload."
    elsewhere = await _upload(api, await _project(api), FILES, binary=False)
    mixed = await api.client.get(
        f"/v1/snapshots/{second['snapshot']}/compare",
        params={"base": str(elsewhere["snapshot"])},
        headers=api.auth,
    )
    assert mixed.status_code == 422 and mixed.json()["code"] == "different_projects"


async def test_other_workspaces_see_nothing(api_factory: ApiFactory) -> None:
    api = await api_factory(demo_enabled=True)
    project = await _project(api)
    upload = await _upload(api, project, FILES)
    ws = await _workspace(api, project)
    ws = (await _save(api, ws, JAVA, "class Order {}\n"))["change_set"]
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    for method, path, params in (
        ("GET", f"/v1/change-sets/{ws['id']}", None),
        ("GET", f"/v1/change-sets/{ws['id']}/file", {"path": JAVA}),
        ("GET", f"/v1/change-sets/{ws['id']}/issues", None),
        ("GET", f"/v1/change-sets/{ws['id']}/export", {"format": "patch"}),
        ("GET", f"/v1/projects/{project}/change-sets", None),
        ("POST", f"/v1/projects/{project}/change-sets", None),
        ("POST", f"/v1/change-sets/{ws['id']}/checks", None),
        ("DELETE", f"/v1/change-sets/{ws['id']}", None),
        (
            "GET",
            f"/v1/snapshots/{upload['snapshot']}/compare",
            {"base": str(upload["snapshot"])},
        ),
    ):
        response = await api.client.request(
            method, path, params=params, headers=ORIGIN, json={} if method == "POST" else None
        )
        assert response.status_code == 404, (method, path, response.text)
    hidden = await api.client.put(
        f"/v1/change-sets/{ws['id']}/file",
        json={"version": ws["version"], "path": JAVA, "content": "x\n"},
        headers=ORIGIN,
    )
    assert hidden.status_code == 404


async def test_deleting_a_workspace_keeps_the_upload(api: ApiHarness) -> None:
    project = await _project(api)
    upload = await _upload(api, project, FILES)
    ws = await _workspace(api, project)
    ws = (await _save(api, ws, JAVA, "class Order {}\n"))["change_set"]
    engine = create_engine_from_settings(api.settings)
    try:
        # A copy that a check scanned (normally created by the worker) goes with the workspace.
        async with transaction(create_session_factory(engine)) as session:
            base = await session.get(Snapshot, upload["snapshot"])
            assert base is not None
            session.add(
                Snapshot(
                    workspace_id=base.workspace_id,
                    project_id=base.project_id,
                    source_id=base.source_id,
                    capture_status=CaptureStatus.FROZEN.value,
                    manifest_sha256="f" * 64,
                    frozen_at=datetime.now(UTC),
                    derived_from=base.id,
                    change_set_id=uuid.UUID(ws["id"]),
                )
            )
        listed = (
            await api.client.get(f"/v1/projects/{project}/snapshots", headers=api.auth)
        ).json()
        assert [s["id"] for s in listed["items"]] == [str(upload["snapshot"])]
        gone = await api.client.delete(f"/v1/change-sets/{ws['id']}", headers=api.auth)
        assert gone.status_code == 204
        missing = await api.client.get(f"/v1/change-sets/{ws['id']}", headers=api.auth)
        assert missing.status_code == 404
        async with transaction(create_session_factory(engine)) as session:
            count = await session.scalar(
                select(func.count()).where(Snapshot.project_id == upload["project"])
            )
            assert count == 1
            assert await session.get(Snapshot, upload["snapshot"]) is not None
    finally:
        await engine.dispose()


LATIN1 = "src/Legacy.java"
LATIN1_BYTES = 'class Legacy { String s = "caf\u00e9"; }\n'.encode("latin-1")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


async def test_encodings_unicode_and_limits(api_factory: ApiFactory, tmp_path: Path) -> None:
    api = await api_factory(intake_max_text_file_bytes=4096, change_set_max_files=3)
    project = await _project(api)
    await _upload(api, project, {**FILES, LATIN1: LATIN1_BYTES})
    ws = await _workspace(api, project)
    # A file that is not UTF-8 is shown as not editable and refused, never re-encoded.
    legacy = (
        await api.client.get(
            f"/v1/change-sets/{ws['id']}/file", params={"path": LATIN1}, headers=api.auth
        )
    ).json()
    assert legacy["editable"] is False and "not UTF-8" in legacy["reason"]
    refused = await api.client.put(
        f"/v1/change-sets/{ws['id']}/file",
        json={"version": ws["version"], "path": LATIN1, "content": "class Legacy {}\n"},
        headers=api.auth,
    )
    assert refused.status_code == 409 and refused.json()["code"] == "not_editable"
    # Unicode (including astral characters) and a byte order mark survive exactly.
    unicode_text = '\ufeffexport const s = "\u20ac \u00fc \u65e5\u672c \U0001f600";\n'
    ws = (await _save(api, ws, "src/keep.js", unicode_text))["change_set"]
    shown = (
        await api.client.get(
            f"/v1/change-sets/{ws['id']}/file", params={"path": "src/keep.js"}, headers=api.auth
        )
    ).json()
    assert shown["content"] == unicode_text
    too_big = await api.client.put(
        f"/v1/change-sets/{ws['id']}/file",
        json={"version": ws["version"], "path": JAVA, "content": "x" * 5000},
        headers=api.auth,
    )
    assert too_big.status_code == 413
    ws = (await _save(api, ws, JAVA, "class Order {}\n"))["change_set"]
    ws = (await _save(api, ws, WIN, "const a = 2;\n"))["change_set"]
    full = await api.client.put(
        f"/v1/change-sets/{ws['id']}/file",
        json={"version": ws["version"], "path": "src/Fourth.java", "content": "class F {}\n"},
        headers=api.auth,
    )
    assert full.status_code == 409 and full.json()["code"] == "workspace_full"
    # The full project ZIP copies unchanged files byte for byte, whatever their encoding.
    archive = await api.client.get(
        f"/v1/change-sets/{ws['id']}/export", params={"format": "full"}, headers=api.auth
    )
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert zipped.read(LATIN1) == LATIN1_BYTES
        assert zipped.read("src/keep.js") == unicode_text.encode()
        assert zipped.getinfo(JAVA).external_attr >> 16 == 0o100644
    # The patch round-trips the Unicode file exactly.
    patch = await api.client.get(
        f"/v1/change-sets/{ws['id']}/export", params={"format": "patch"}, headers=api.auth
    )
    tree = tmp_path / "tree"
    _tree(tree, {k: v for k, v in FILES.items()})
    (tree / LATIN1).write_bytes(LATIN1_BYTES)
    (tmp_path / "u.diff").write_bytes(patch.content)
    assert _git_apply(tree, tmp_path / "u.diff", check_only=False).returncode == 0
    assert (tree / "src/keep.js").read_bytes() == unicode_text.encode()


async def test_git_am_commit_and_workspace_states(api_factory: ApiFactory, tmp_path: Path) -> None:
    gateway = RecordingGateway()
    api = await api_factory(gateway=gateway)
    project = await _project(api)
    await _upload(api, project, FILES)
    ws = await _workspace(api, project, title="Fix the café checkout")
    assert ws["state"] == "draft"
    edited = JAVA_TEXT.replace('status == "PAID"', '"PAID".equals(status)')
    ws = (await _save(api, ws, JAVA, edited))["change_set"]
    ws = (
        await api.client.post(
            f"/v1/change-sets/{ws['id']}/file/delete",
            json={"version": ws["version"], "path": GONE},
            headers=api.auth,
        )
    ).json()
    started = (await api.client.post(f"/v1/change-sets/{ws['id']}/checks", headers=api.auth)).json()
    assert (await api.client.get(f"/v1/change-sets/{ws['id']}", headers=api.auth)).json()[
        "state"
    ] == "checking"
    engine = create_engine_from_settings(api.settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            row = await session.get(ChangeSetCheck, uuid.UUID(started["id"]))
            assert row is not None
            row.state = "SUCCEEDED"
            row.result = {"counts": {"fixed": 1, "new": 0}, "outcomes": {}}
    finally:
        await engine.dispose()
    assert (await api.client.get(f"/v1/change-sets/{ws['id']}", headers=api.auth)).json()[
        "state"
    ] == "ready"

    mbox = await api.client.get(
        f"/v1/change-sets/{ws['id']}/export", params={"format": "mbox"}, headers=api.auth
    )
    assert mbox.status_code == 200 and mbox.headers["content-type"].startswith("application/mbox")
    repo = tmp_path / "repo"
    _tree(repo, FILES)
    assert _git(repo, "init", "-q").returncode == 0
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-qm", "upload").returncode == 0
    (tmp_path / "ws.patch").write_bytes(mbox.content)
    applied = _git(repo, "am", str(tmp_path / "ws.patch"))
    assert applied.returncode == 0, applied.stderr
    subject = _git(repo, "log", "-1", "--format=%s").stdout.strip()
    assert subject == "Fix the café checkout"
    assert (repo / JAVA).read_text() == edited
    assert not (repo / GONE).exists()
    assert (await api.client.get(f"/v1/change-sets/{ws['id']}", headers=api.auth)).json()[
        "state"
    ] == "exported"
    # Any new change makes it a draft again.
    ws = (await api.client.get(f"/v1/change-sets/{ws['id']}", headers=api.auth)).json()
    ws = (await _save(api, ws, WIN, "const a = 3;\n"))["change_set"]
    assert ws["state"] == "draft"
