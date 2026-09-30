"""Validated fixes (P05) on the real stack: a finding from a real scan gets a deterministic fix,
the validation ladder runs in the Temporal worker and in the lite in-process runner with the real
engines, and the downloaded patch applies to exactly the uploaded code. Synthetic fixtures only.
The uploads carry hostile build scripts, test files and analyzer configuration that would leave a
mark if anything ran them: no project code is executed by scanning or by fix validation."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from crp_analysis.engines.base import (
    Availability,
    CacheIdentity,
    CancelToken,
    EngineAdapter,
    EngineOutcome,
    Heartbeat,
)
from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory
from crp_worker.scan import default_adapters

from .conftest import lite_stack

pytestmark = pytest.mark.integration

StackFactory = Callable[..., contextlib.AbstractAsyncContextManager[Any]]
INVOICE = "src/main/java/com/example/billing/InvoiceService.java"
HOSTILE = {
    "package.json",
    "eslint.config.mjs",
    "web/src/cart.test.js",
    "Makefile",
    "gradlew",
    "build.gradle",
    "src/test/java/com/example/billing/InvoiceServiceTest.java",
}


def _digest(root: Path) -> str:
    return hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file())
    ).hexdigest()


def _git_apply(tree: Path, check: bool) -> subprocess.CompletedProcess[bytes]:
    args = ["git", "apply", "-p1", "fix.diff"]
    if check:
        args.insert(2, "--check")
    return subprocess.run(args, cwd=tree, capture_output=True, check=False)  # noqa: S603


def _add_hostile_files(root: Path, marks: Path) -> None:
    """Build hooks, tests and analyzer configs that each create a file in ``marks`` if run."""

    def touch(name: str) -> str:
        return f"touch {shlex.quote(str(marks / name))}"

    def js(name: str) -> str:
        return f'require("node:fs").writeFileSync({json.dumps(str(marks / name))}, "ran");\n'

    package = json.loads((root / "package.json").read_text())
    hooks = ("preinstall", "install", "postinstall", "prepare", "build", "test")
    package["scripts"] = {hook: touch(f"npm-{hook}") for hook in hooks}
    (root / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    (root / "eslint.config.mjs").write_text(
        'import { writeFileSync } from "node:fs";\n'
        f'writeFileSync({json.dumps(str(marks / "eslint-config"))}, "ran");\n'
        "export default [];\n"
    )
    (root / "web" / "src" / "cart.test.js").write_text(js("js-test") + 'test("t", () => {});\n')
    (root / "Makefile").write_text(f"all:\n\t{touch('make')}\n")
    (root / "gradlew").write_text(f"#!/bin/sh\n{touch('gradlew')}\n")
    (root / "build.gradle").write_text(
        f"task mark(type: Exec) {{ commandLine 'touch', '{marks / 'gradle'}' }}\n"
    )
    tests = root / "src" / "test" / "java" / "com" / "example" / "billing"
    tests.mkdir(parents=True)
    (tests / "InvoiceServiceTest.java").write_text(
        "package com.example.billing;\n\nclass InvoiceServiceTest {\n"
        "    static {\n        try {\n"
        f"            new java.io.File({json.dumps(str(marks / 'java-test'))}).createNewFile();\n"
        "        } catch (java.io.IOException e) {\n            throw new RuntimeException(e);\n"
        "        }\n    }\n}\n"
    )


async def _wait(stack: Any, validation_proposal: str) -> dict[str, Any]:
    for _ in range(300):
        fix = await stack.ok("GET", f"/v1/fix-proposals/{validation_proposal}")
        latest = fix["latest_validation"]
        if latest and latest["state"] in {"PASSED", "FAILED", "CANCELED"}:
            return dict(fix)
        await asyncio.sleep(0.3)
    raise AssertionError("fix validation did not finish")


async def _fix_and_validate(stack: Any, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    source = prepare_fixture("seeded-mixed", tmp_path / "upload")
    marks = tmp_path / "marks"
    marks.mkdir()
    _add_hostile_files(source, marks)
    before = _digest(source)
    project = await stack.project("P05 fixes")
    intake = await stack.zip_intake(project, zip_directory(source))
    files = await stack.ok("GET", f"/v1/snapshots/{intake['snapshot_id']}/files?limit=500")
    kept = {f["path"] for f in files["items"] if f["disposition"] != "EXCLUDED"}
    assert kept >= HOSTILE, HOSTILE - kept  # the hostile files really are part of the upload
    scan = await stack.scan_and_wait(project, intake["snapshot_id"])
    findings = await stack.findings(scan["id"])
    target = next(f for f in findings if f["rule_id"] == "UseEqualsToCompareStrings")
    options = await stack.ok("GET", f"/v1/findings/{target['id']}/fix-options")
    assert [o["available"] for o in options["options"]] == [True]
    fix = await stack.ok(
        "POST",
        f"/v1/findings/{target['id']}/fix-proposals",
        json={"recipe_id": "java:string-literal-equals"},
    )
    await stack.ok("POST", f"/v1/fix-proposals/{fix['id']}/validations")
    done = await _wait(stack, fix["id"])
    assert _digest(source) == before  # the uploaded folder was never touched
    assert sorted(p.name for p in marks.iterdir()) == []  # no build, test or config code ran
    return done, source


async def _check_patch(stack: Any, fix: dict[str, Any], source: Path, tmp_path: Path) -> None:
    patch = await stack.client.get(f"/v1/fix-proposals/{fix['id']}/patch")
    tree = tmp_path / "apply"
    await asyncio.to_thread(shutil.copytree, source, tree)
    (tree / "fix.diff").write_text(patch.text)
    assert (await asyncio.to_thread(_git_apply, tree, True)).returncode == 0
    assert (await asyncio.to_thread(_git_apply, tree, False)).returncode == 0
    assert '"PAID".equals(status)' in (tree / INVOICE).read_text()
    assert hashlib.sha256((tree / INVOICE).read_bytes()).hexdigest() == fix["result_sha256"]


async def test_fix_is_validated_by_the_temporal_worker(
    settings: Settings, stack_factory: StackFactory, tmp_path: Path
) -> None:
    async with stack_factory(settings) as stack:
        fix, source = await _fix_and_validate(stack, tmp_path)
        validation = fix["latest_validation"]
        assert validation["state"] == "PASSED", validation
        assert fix["state"] == "VALIDATED" and validation["current"] is True
        states = {s["id"]: s["state"] for s in validation["steps"]}
        assert states == {
            "integrity": "passed",
            "syntax": "passed",
            "checks": "passed",
            "tests": "not_run",
            "build": "not_run",
        }
        details = {s["id"]: s["detail"] for s in validation["steps"]}
        assert "no isolated runner" in details["tests"]
        assert "build toolchain and dependencies" in details["build"]
        assert "Not compiled, built or tested" in " ".join(fix["labels"])
        await _check_patch(stack, fix, source, tmp_path)

        # Editing resets the validation; an edit that keeps the problem fails the ladder.
        line = fix["edits"][0]
        edited = await stack.ok(
            "PUT",
            f"/v1/fix-proposals/{fix['id']}/edits",
            json={
                "version": fix["version"],
                "edits": [
                    {
                        "start_line": line["start_line"],
                        "replacement": [line["original"][0] + " "],
                    }
                ],
            },
        )
        assert edited["state"] == "PROPOSED" and edited["latest_validation"]["current"] is False
        await stack.ok("POST", f"/v1/fix-proposals/{fix['id']}/validations")
        failed = await _wait(stack, fix["id"])
        assert failed["state"] == "VALIDATION_FAILED"
        checks = next(s for s in failed["latest_validation"]["steps"] if s["id"] == "checks")
        assert checks["state"] == "failed" and "still reported" in checks["detail"]


async def test_fix_is_validated_on_the_lite_profile(settings: Settings, tmp_path: Path) -> None:
    async with lite_stack(settings) as stack:
        fix, source = await _fix_and_validate(stack, tmp_path)
        assert fix["latest_validation"]["state"] == "PASSED", fix["latest_validation"]
        assert fix["state"] == "VALIDATED"
        await _check_patch(stack, fix, source, tmp_path)


async def test_salesforce_fix_leaves_org_checks_not_run(settings: Settings, tmp_path: Path) -> None:
    source = prepare_fixture("salesforce-mixed", tmp_path / "upload")
    async with lite_stack(settings) as stack:
        project = await stack.project("P05 Salesforce fix")
        intake = await stack.zip_intake(project, zip_directory(source))
        scan = await stack.scan_and_wait(project, intake["snapshot_id"])
        target = next(
            f
            for f in await stack.findings(scan["id"])
            if f["rule_id"] == "crp.sf.metadata.retired-api-version"
        )
        fix = await stack.ok(
            "POST",
            f"/v1/findings/{target['id']}/fix-proposals",
            json={"recipe_id": "salesforce:api-version"},
        )
        assert fix["edits"][0]["replacement"] == ["    <apiVersion>62.0</apiVersion>"]
        await stack.ok("POST", f"/v1/fix-proposals/{fix['id']}/validations")
        done = await _wait(stack, fix["id"])
        validation = done["latest_validation"]
        assert validation["state"] == "PASSED", validation
        steps = {s["id"]: s for s in validation["steps"]}
        assert steps["checks"]["state"] == "passed" and "frameworks" in steps["checks"]["detail"]
        # Deploying and running Apex tests needs an authorized org; it is reported, not faked.
        assert steps["build"]["state"] == "not_run"
        assert "authorized Salesforce org" in steps["build"]["detail"]
        assert steps["tests"]["state"] == "not_run"


class _Held:
    """The real PMD adapter that, once ``hold`` is set, waits for cancellation before running."""

    def __init__(self, inner: EngineAdapter) -> None:
        self._inner = inner
        self.name = inner.name
        self.ruleset_id = inner.ruleset_id
        self.hold = False
        self.entered = threading.Event()

    def is_eligible(self, path: str, language: str | None) -> bool:
        return self._inner.is_eligible(path, language)

    def availability(self) -> Availability:
        return self._inner.availability()

    def enabled_rules(self) -> tuple[str, ...] | None:
        return self._inner.enabled_rules()

    def cache_identity(self) -> CacheIdentity | None:
        return self._inner.cache_identity()

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        if self.hold:
            self.entered.set()
            deadline = time.monotonic() + 60
            while not cancel.cancelled and time.monotonic() < deadline:
                time.sleep(0.1)
        return self._inner.run(root, files, cancel=cancel, heartbeat=heartbeat)


async def test_running_validation_stops_and_can_run_again(
    settings: Settings, stack_factory: StackFactory, tmp_path: Path
) -> None:
    pmd = _Held(default_adapters(settings)["pmd"])
    async with stack_factory(settings, adapters={"pmd": pmd}) as stack:
        source = prepare_fixture("seeded-mixed", tmp_path / "upload")
        project = await stack.project("P05 stop")
        intake = await stack.zip_intake(project, zip_directory(source))
        scan = await stack.scan_and_wait(project, intake["snapshot_id"])
        target = next(
            f
            for f in await stack.findings(scan["id"])
            if f["rule_id"] == "UseEqualsToCompareStrings"
        )
        fix = await stack.ok(
            "POST",
            f"/v1/findings/{target['id']}/fix-proposals",
            json={"recipe_id": "java:string-literal-equals"},
        )
        pmd.hold = True
        started = await stack.ok("POST", f"/v1/fix-proposals/{fix['id']}/validations")
        assert await asyncio.to_thread(pmd.entered.wait, 60)  # the checks are running
        await stack.ok("POST", f"/v1/fix-validations/{started['id']}/cancel")
        await stack.ok("POST", f"/v1/fix-validations/{started['id']}/cancel")  # idempotent
        stopped = await _wait(stack, fix["id"])
        assert stopped["latest_validation"]["state"] == "CANCELED", stopped["latest_validation"]
        assert stopped["state"] == "PROPOSED" and stopped["validations_used"] == 1

        pmd.hold = False
        await stack.ok("POST", f"/v1/fix-proposals/{fix['id']}/validations")
        again = await _wait(stack, fix["id"])
        assert again["latest_validation"]["state"] == "PASSED", again["latest_validation"]
        assert again["state"] == "VALIDATED" and again["validations_used"] == 2
