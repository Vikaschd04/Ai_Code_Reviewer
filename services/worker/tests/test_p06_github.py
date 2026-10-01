"""GitHub reviews (P06) on the real stack against the labelled fake GitHub.

The API, worker, Temporal (and the lite in-process runner), PostgreSQL and every analyzer are real.
Only GitHub is the test double in ``crp_devtools.testing.fake_github``. Covered:
- verified linking;
- captures that match the commit exactly, even when the archive hides files;
- pull request reviews against the merge base, with renames and deletions;
- publication;
- concurrent and out-of-order events;
- a changed merge base;
- configuration-only changes;
- partial scans, provider outages and revoked access;
- forks;
- fix pull requests with an exact-head freshness check.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import update

from crp_analysis.engines.base import (
    Availability,
    CacheIdentity,
    CancelToken,
    EngineAdapter,
    EngineOutcome,
    Heartbeat,
)
from crp_analysis.engines.pmd import PmdAdapter
from crp_core.config import Settings
from crp_core.db.models import GitConnection
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_devtools.testing.fake_github import (
    Executable,
    FakeGitHub,
    FakePull,
    FakeRepo,
    Submodule,
    Symlink,
    generate_app_key,
)
from crp_worker.git_review import reconcile_due
from crp_worker.scan import default_adapters

from .conftest import lite_stack

pytestmark = pytest.mark.integration

StackFactory = Callable[..., contextlib.AbstractAsyncContextManager[Any]]
TERMINAL = {"SUCCEEDED", "PARTIAL", "FAILED", "SUPERSEDED", "SKIPPED", "CANCELED"}
INSTALLATION, REPO_ID = 9001, 101
LFS = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"f" * 64 + b"\nsize 4242\n"
ORDERS = (
    "package shop;\n\npublic class Orders {\n    public boolean paid(String status) {\n"
    '        return status == "PAID";\n    }\n}\n'
)
ORDERS_MORE = ORDERS.replace(
    "}\n}\n",
    '}\n\n    public boolean bulk(String kind) {\n        return kind == "BULK";\n    }\n}\n',
)
LEGACY = (
    "package shop;\n\npublic class Legacy {\n    public boolean old(String code) {\n"
    '        return code == "OLD";\n    }\n}\n'
)
BILLING = (
    "package shop;\n\npublic class Billing {\n    public boolean due(String state) {\n"
    '        return state == "DUE";\n    }\n}\n'
)
HIDDEN = (
    "package shop.internal;\n\npublic class Hidden {\n    public boolean secret(String key) {\n"
    '        return key == "SECRET";\n    }\n}\n'
)
POM = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
    "  <modelVersion>4.0.0</modelVersion>\n  <groupId>acme</groupId>\n"
    "  <artifactId>shop</artifactId>\n  <version>1.0.0</version>\n</project>\n"
)
POM_VULNERABLE = POM.replace(
    "</project>",
    "  <dependencies>\n    <dependency>\n      <groupId>org.apache.logging.log4j</groupId>\n"
    "      <artifactId>log4j-core</artifactId>\n      <version>2.14.1</version>\n"
    "    </dependency>\n  </dependencies>\n</project>",
)
BASE_FILES: dict[str, Any] = {
    ".gitattributes": "src/main/java/shop/internal/* export-ignore\n",
    "README.md": "# Shop\n",
    "pom.xml": POM,
    "src/main/java/shop/Orders.java": ORDERS,
    "src/main/java/shop/Legacy.java": LEGACY,
    "src/main/java/shop/Billing.java": BILLING,
    "src/main/java/shop/internal/Hidden.java": HIDDEN,
    "docs/readme-link": Symlink("../README.md"),
    "vendor/sdk": Submodule("c" * 40),
    "assets/intro.mp4": LFS,
    "scripts/build.sh": Executable(b"#!/bin/sh\nmvn -q package\n"),
}


@dataclass
class World:
    fake: FakeGitHub
    repo: FakeRepo
    secret: str
    settings: Settings
    main: str


@pytest.fixture
def world(settings: Settings) -> Iterator[World]:
    private, public = generate_app_key()
    fake = FakeGitHub(public_key_pem=public).start()
    repo = fake.add_repo(REPO_ID, "acme", "shop")
    main = repo.commit("main", BASE_FILES, "initial")
    fake.add_installation(INSTALLATION, "acme", [REPO_ID])
    fake.add_user("octo", [INSTALLATION])
    secret = "whsec-" + secrets.token_hex(16)
    configured = settings.model_copy(
        update={
            "github_app_id": fake.app_id,
            "github_client_id": fake.client_id,
            "github_client_secret": SecretStr(fake.client_secret),
            "github_private_key": SecretStr(private.decode()),
            "github_webhook_secret": SecretStr(secret),
            "github_app_slug": fake.slug,
            "github_api_url": fake.base_url,
            "github_web_url": fake.base_url,
        }
    )
    try:
        yield World(fake, repo, secret, configured, main)
    finally:
        fake.stop()


# -- helpers ---------------------------------------------------------------------------------------


async def link(stack: Any, world: World) -> dict[str, Any]:
    world.fake.callback_url = f"{stack.server.url}/v1/github/callback"
    start = await stack.ok("POST", f"/v1/workspaces/{stack.workspace_id}/github/link")
    async with httpx.AsyncClient() as browser:
        consent = await browser.get(start["authorize_url"])
    assert consent.status_code == 302
    back = await stack.client.get(consent.headers["location"])
    assert back.status_code == 303
    fragment = back.headers["location"].split("#", 1)[1]
    assert fragment.startswith("/github?")
    query = parse_qs(fragment.split("?", 1)[1])
    body = {"code": query["code"][0], "state": query["state"][0]}
    linked = await stack.ok(
        "POST", f"/v1/workspaces/{stack.workspace_id}/github/link/complete", json=body
    )
    reused = await stack.client.post(
        f"/v1/workspaces/{stack.workspace_id}/github/link/complete", json=body
    )
    assert reused.status_code == 409 and reused.json()["code"] == "link_state_used"
    return dict(linked)


async def connect(stack: Any, world: World) -> tuple[str, dict[str, Any]]:
    linked = await link(stack, world)
    repo_id = linked["linked"][0]["repositories"][0]["id"]
    project = await stack.project(f"Shop {secrets.token_hex(3)}")
    connection = await stack.ok(
        "PUT", f"/v1/projects/{project}/git-connection", json={"repository_id": repo_id}
    )
    assert connection["connected"] and connection["status"] == "active"
    first = (await stack.ok("GET", f"/v1/projects/{project}/code-reviews"))["items"]
    assert len(first) == 1
    return project, await wait_review(stack, first[0]["id"])


async def wait_review(stack: Any, review_id: str, limit: float = 240) -> dict[str, Any]:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        review = await stack.ok("GET", f"/v1/code-reviews/{review_id}")
        if review["state"] in TERMINAL:
            return dict(review)
        await asyncio.sleep(0.3)
    raise AssertionError(f"review {review_id} did not finish")


async def deliver(
    stack: Any, world: World, event: str, payload: dict[str, Any], **kwargs: Any
) -> httpx.Response:
    url = f"{stack.server.url}/v1/github/webhook"
    return await asyncio.to_thread(world.fake.deliver, url, world.secret, event, payload, **kwargs)


async def push(stack: Any, world: World, after: str, before: str | None = None) -> str:
    response = await deliver(
        stack, world, "push", world.fake.push_payload(world.repo, INSTALLATION, before, after)
    )
    assert response.status_code == 202, response.text
    return str(response.json()["reviews"][0])


def open_pull(
    world: World, number: int, changes: dict[str, Any], *, fork: int | None = None
) -> str:
    head = world.repo.commit(f"feature-{number}", changes, f"pull {number}", base=world.main)
    world.repo.pulls[number] = FakePull(
        number, f"feature-{number}", head, "main", f"Change #{number}", "dev", head_repo_id=fork
    )
    return head


async def findings(stack: Any, scan_id: str) -> list[dict[str, Any]]:
    return list(await stack.findings(scan_id))


async def issues(stack: Any, project: str) -> list[dict[str, Any]]:
    return list((await stack.ok("GET", f"/v1/projects/{project}/issues?limit=200"))["items"])


# -- tests -------------------------------------------------------------------------------------------


async def test_connected_repository_is_reviewed_exactly_as_committed(
    world: World, stack_factory: StackFactory
) -> None:
    async with stack_factory(world.settings) as stack:
        status = await stack.ok("GET", "/v1/github/status")
        assert status["available"] and status["linking_available"] and status["webhooks_available"]
        project, review = await connect(stack, world)
        assert review["state"] == "SUCCEEDED", review
        assert review["kind"] == "branch" and review["trigger"] == "manual" and review["full"]
        assert review["head_sha"] == world.main and review["repository"] == "acme/shop"
        assert review["publish_state"] == "off" and not world.repo.check_runs
        snapshot = await stack.ok("GET", f"/v1/snapshots/{review['head_snapshot_id']}")
        assert snapshot["source_mode"] == "github" and snapshot["git_commit"] == world.main
        assert snapshot["git_ref"] == "main" and snapshot["git_repository"] == "acme/shop"
        assert snapshot["git_tree_sha"] == world.repo.commits[world.main].tree
        capture = snapshot["git_capture"]
        # export-ignore hid Hidden.java from GitHub's archive: it was fetched as committed.
        assert capture["fetched"]["paths"] == ["src/main/java/shop/internal/Hidden.java"]
        assert capture["submodules"]["count"] == 1 and capture["git_lfs"]["count"] == 1
        assert capture["symlinks"] == 1 and capture["executable"] == ["scripts/build.sh"]
        files = (await stack.ok("GET", f"/v1/snapshots/{snapshot['id']}/files?limit=200"))["items"]
        reasons = {f["path"]: f["reason"] for f in files if f["disposition"] == "EXCLUDED"}
        assert reasons["vendor/sdk/"] == "submodule"
        assert reasons["assets/intro.mp4"] == "git_lfs"
        assert reasons["docs/readme-link"] == "symlink"
        found = await findings(stack, review["head_scan_id"])
        hits = {f["path"] for f in found if f["rule_id"] == "UseEqualsToCompareStrings"}
        assert "src/main/java/shop/internal/Hidden.java" in hits  # the hidden file was reviewed
        assert review["result"]["new"] == len(found) and review["result"]["compared_with"] is None
        assert len(await issues(stack, project)) == len(found)  # default branch: issues created
        scan = await stack.ok("GET", f"/v1/scans/{review['head_scan_id']}")
        assert scan["mode"] == "baseline" and scan["lifecycle_applied"]


async def test_pull_request_review_compares_with_the_merge_base(
    world: World, stack_factory: StackFactory
) -> None:
    async with stack_factory(world.settings) as stack:
        project, _ = await connect(stack, world)
        before = await issues(stack, project)
        head = open_pull(
            world,
            7,
            {
                "src/main/java/shop/Legacy.java": None,
                "src/main/java/shop/old/Legacy.java": LEGACY,  # renamed, identical
                "src/main/java/shop/Billing.java": None,  # deleted
                "src/main/java/shop/Orders.java": ORDERS_MORE,  # one new problem
            },
        )
        payload = world.fake.pull_payload(world.repo, INSTALLATION, "opened", 7)
        accepted = await deliver(stack, world, "pull_request", payload, delivery="delivery-pr-7-a")
        assert accepted.status_code == 202
        again = await deliver(stack, world, "pull_request", payload, delivery="delivery-pr-7-a")
        assert again.status_code == 200 and again.json()["outcome"] == "duplicate"
        forged = await deliver(
            stack, world, "pull_request", payload, signature="sha256=" + "0" * 64
        )
        assert forged.status_code == 401
        review = await wait_review(stack, accepted.json()["reviews"][0])
        assert review["state"] == "SUCCEEDED", review
        assert review["kind"] == "pull_request" and review["pr_number"] == 7
        assert review["head_sha"] == head and review["merge_base_sha"] == world.main
        changes = review["changes"]
        assert changes["renamed"]["pairs"] == [
            {"from": "src/main/java/shop/Legacy.java", "to": "src/main/java/shop/old/Legacy.java"}
        ]
        assert changes["removed"]["paths"] == ["src/main/java/shop/Billing.java"]
        assert changes["modified"]["paths"] == ["src/main/java/shop/Orders.java"]
        result = review["result"]
        # Only the new method's line is new (PMD reports two rules there); the renamed file's
        # findings are not new.
        assert {(i["path"], i["line"]) for i in result["new_items"]} == {
            ("src/main/java/shop/Orders.java", 9)
        }
        assert result["new"] == len(result["new_items"]) == 2
        assert result["fixed"] == 0 and result["not_rechecked"] >= 1  # deleted: not "fixed"
        listed = await stack.ok("GET", f"/v1/projects/{project}/code-reviews?pull_request=7")
        assert [r["id"] for r in listed["items"]] == [review["id"]]
        scan = await stack.ok("GET", f"/v1/scans/{review['head_scan_id']}")
        assert scan["mode"] == "pull_request" and not scan["lifecycle_applied"]
        after = await issues(stack, project)
        assert {(i["id"], i["status"], i["recheck_state"]) for i in after} == {
            (i["id"], i["status"], i["recheck_state"]) for i in before
        }  # a pull request never changes the project's issues


async def test_publication_posts_one_check_and_keeps_one_comment(
    world: World, stack_factory: StackFactory
) -> None:
    async with stack_factory(world.settings) as stack:
        project, _ = await connect(stack, world)
        connection = await stack.ok("GET", f"/v1/projects/{project}/git-connection")
        # The finished full review recorded its time without bumping the policy version, so an
        # admin who loaded the settings earlier can still save them.
        assert connection["version"] == 1 and connection["last_full_review_at"] is not None
        updated = await stack.ok(
            "PATCH",
            f"/v1/projects/{project}/git-connection",
            json={
                "version": connection["version"],
                "publish_checks": True,
                "check_fail_threshold": "high",
            },
        )
        assert updated["publish_checks"] and updated["version"] == connection["version"] + 1
        stale = await stack.client.patch(
            f"/v1/projects/{project}/git-connection",
            json={"version": connection["version"], "review_forks": True},
        )
        assert stale.status_code == 409
        head = open_pull(world, 8, {"src/main/java/shop/Orders.java": ORDERS_MORE})
        payload = world.fake.pull_payload(world.repo, INSTALLATION, "opened", 8)
        review = await wait_review(
            stack, (await deliver(stack, world, "pull_request", payload)).json()["reviews"][0]
        )
        assert review["state"] == "SUCCEEDED" and review["publish_state"] == "published", review
        runs = list(world.repo.check_runs.values())
        assert len(runs) == 1 and runs[0]["head_sha"] == head
        assert runs[0]["name"] == "refactorX" and runs[0]["conclusion"] == "failure"  # new high
        annotations = runs[0]["output"]["annotations"]
        assert {(a["path"], a["start_line"]) for a in annotations} == {
            ("src/main/java/shop/Orders.java", 9)
        }
        assert len(annotations) == review["result"]["new"] == 2
        comments = list(world.repo.comments.values())
        assert len(comments) == 1 and comments[0]["number"] == 8
        assert comments[0]["body"].startswith("<!-- refactorx:review -->")
        assert "2 new problems in this pull request" in comments[0]["body"]
        # A new commit on the pull request: a new check for the new head, the same comment updated.
        fixed = ORDERS_MORE.replace('kind == "BULK"', '"BULK".equals(kind)')
        new_head = world.repo.commit("feature-8", {"src/main/java/shop/Orders.java": fixed})
        world.repo.pulls[8].head_sha = new_head
        sync = world.fake.pull_payload(world.repo, INSTALLATION, "synchronize", 8)
        second = await wait_review(
            stack, (await deliver(stack, world, "pull_request", sync)).json()["reviews"][0]
        )
        assert second["state"] == "SUCCEEDED" and second["result"]["new"] == 0
        assert len(world.repo.comments) == 1
        comment = next(iter(world.repo.comments.values()))
        assert comment["updates"] == 1 and "No new problems" in comment["body"]
        latest = [r for r in world.repo.check_runs.values() if r["head_sha"] == new_head]
        assert len(latest) == 1 and latest[0]["conclusion"] == "success"


async def test_events_converge_on_the_newest_commit(
    world: World, stack_factory: StackFactory
) -> None:
    async with stack_factory(world.settings) as stack:
        project, first = await connect(stack, world)
        c1 = world.repo.commit("main", {"README.md": "# Shop\n\nOne\n"}, "one")
        c2 = world.repo.commit("main", {"README.md": "# Shop\n\nTwo\n"}, "two")
        # GitHub delivered the newer push first and the older one late.
        newer = await push(stack, world, c2, c1)
        older = await push(stack, world, c1, world.main)
        done = [await wait_review(stack, r) for r in (newer, older)]
        assert {r["head_sha"] for r in done} == {c2}  # nobody reviewed the older commit
        states = sorted(r["state"] for r in done)
        assert states == ["SKIPPED", "SUCCEEDED"], [(r["state"], r["error_code"]) for r in done]
        skipped = next(r for r in done if r["state"] == "SKIPPED")
        assert skipped["error_code"] == "already_reviewed"
        winner = next(r for r in done if r["state"] == "SUCCEEDED")
        assert winner["base_sha"] == first["head_sha"]  # compared with the last reviewed commit
        other_branch = world.repo.commit("topic", {"README.md": "x\n"}, "topic", base=c2)
        ignored = await deliver(
            stack,
            world,
            "push",
            world.fake.push_payload(world.repo, INSTALLATION, c2, other_branch, branch="topic"),
        )
        assert ignored.json()["outcome"] == "ignored"
        assert project


class _Held:
    """Real PMD that, while ``hold`` is set, waits for cancellation before running."""

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
        return None  # always run, so a held scan really waits

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        if self.hold:
            self.entered.set()
            deadline = time.monotonic() + 90
            while not cancel.cancelled and time.monotonic() < deadline:
                time.sleep(0.1)
        return self._inner.run(root, files, cancel=cancel, heartbeat=heartbeat)


async def test_a_newer_push_supersedes_a_running_review(
    world: World, stack_factory: StackFactory
) -> None:
    pmd = _Held(default_adapters(world.settings)["pmd"])
    async with stack_factory(world.settings, adapters={"pmd": pmd}) as stack:
        project, _ = await connect(stack, world)
        before = await issues(stack, project)
        pmd.hold = True
        c1 = world.repo.commit("main", {"src/main/java/shop/Billing.java": None}, "drop billing")
        first = await push(stack, world, c1)
        assert await asyncio.to_thread(pmd.entered.wait, 60)  # the review of c1 is scanning
        pmd.hold = False
        c2 = world.repo.commit("main", {"README.md": "# Shop v2\n"}, "docs")
        second = await push(stack, world, c2, c1)
        superseded = await wait_review(stack, first)
        winner = await wait_review(stack, second)
        assert superseded["state"] == "SUPERSEDED" and superseded["superseded_by"] == second
        old_scan = await stack.ok("GET", f"/v1/scans/{superseded['head_scan_id']}")
        assert old_scan["state"] == "CANCELED" and not old_scan["lifecycle_applied"]
        assert winner["state"] == "SUCCEEDED" and winner["head_sha"] == c2
        after = {i["path"]: i for i in await issues(stack, project)}
        billing = next(i for i in before if i["path"].endswith("Billing.java"))
        # Billing.java is gone at c2 too: the winning review re-evaluated its issue, which cannot
        # be verified absent (deleted file) and therefore stays open.
        assert after[billing["path"]]["recheck_state"] == "UNKNOWN"
        assert after[billing["path"]]["status"] == "OPEN"


async def test_a_changed_merge_base_is_followed(world: World, stack_factory: StackFactory) -> None:
    async with stack_factory(world.settings) as stack:
        await connect(stack, world)
        open_pull(world, 9, {"src/main/java/shop/Orders.java": ORDERS_MORE})
        payload = world.fake.pull_payload(world.repo, INSTALLATION, "opened", 9)
        first = await wait_review(
            stack, (await deliver(stack, world, "pull_request", payload)).json()["reviews"][0]
        )
        assert first["merge_base_sha"] == world.main
        # main moves on (Billing.java fixed there); the pull request is rebased onto it.
        fixed_billing = BILLING.replace('state == "DUE"', '"DUE".equals(state)')
        new_main = world.repo.commit("main", {"src/main/java/shop/Billing.java": fixed_billing})
        rebased = world.repo.commit(
            "feature-9", {"src/main/java/shop/Orders.java": ORDERS_MORE}, "rebased", base=new_main
        )
        world.repo.pulls[9].head_sha = rebased
        sync = world.fake.pull_payload(world.repo, INSTALLATION, "synchronize", 9)
        second = await wait_review(
            stack, (await deliver(stack, world, "pull_request", sync)).json()["reviews"][0]
        )
        assert second["state"] == "SUCCEEDED" and second["merge_base_sha"] == new_main
        assert second["changes"]["modified"]["paths"] == ["src/main/java/shop/Orders.java"]
        new_lines = {(i["path"], i["line"]) for i in second["result"]["new_items"]}
        assert new_lines == {("src/main/java/shop/Orders.java", 9)}  # Billing: not the PR's


async def test_a_configuration_only_change_reaches_dependency_checks(
    world: World, stack_factory: StackFactory
) -> None:
    async with stack_factory(world.settings) as stack:
        await connect(stack, world)
        open_pull(world, 10, {"pom.xml": POM_VULNERABLE})
        payload = world.fake.pull_payload(world.repo, INSTALLATION, "opened", 10)
        review = await wait_review(
            stack, (await deliver(stack, world, "pull_request", payload)).json()["reviews"][0]
        )
        assert review["state"] == "SUCCEEDED", review
        assert review["changes"]["configuration"]["paths"] == ["pom.xml"]
        new = review["result"]["new_items"]
        assert new and {i["engine"] for i in new} == {"trivy"} and new[0]["path"] == "pom.xml"
        # Unchanged files were not analysed again: their per-file results were reused.
        assert review["result"]["cache"]["hits"] > 0


async def test_partial_scans_outages_and_revoked_access(
    world: World, stack_factory: StackFactory, tmp_path: Path
) -> None:
    home = tmp_path / "fake-pmd"
    (home / "bin").mkdir(parents=True)
    (home / "lib").mkdir()
    (home / "lib" / "pmd-core-0.0.0.jar").write_bytes(b"")
    (home / "bin" / "pmd").write_text("#!/bin/sh\necho 'fatal: simulated PMD crash' >&2\nexit 1\n")
    (home / "bin" / "pmd").chmod(0o755)
    crashing = PmdAdapter(home, java_heap="256m", timeout_seconds=60, max_output_bytes=100_000)
    async with stack_factory(world.settings, adapters={"pmd": crashing}) as stack:
        project, review = await connect(stack, world)
        assert review["state"] == "PARTIAL" and "pmd" in review["result"]["incomplete"]
        connection = await stack.ok("GET", f"/v1/projects/{project}/git-connection")
        await stack.ok(
            "PATCH",
            f"/v1/projects/{project}/git-connection",
            json={"version": connection["version"], "publish_checks": True},
        )
        manual = await stack.ok("POST", f"/v1/projects/{project}/code-reviews", json={})
        partial = await wait_review(stack, manual["id"])
        assert partial["state"] == "PARTIAL" and partial["publish_state"] == "published"
        check = next(iter(world.repo.check_runs.values()))
        assert check["conclusion"] == "neutral"  # incomplete is never "success"
        assert "Not every check finished: pmd" in check["output"]["summary"]
        # GitHub is down: the review fails honestly and can be run again.
        world.fake.fail("/repos/acme/shop/git/ref", 503, times=20)
        outage = await stack.ok("POST", f"/v1/projects/{project}/code-reviews", json={})
        failed = await wait_review(stack, outage["id"])
        assert failed["state"] == "FAILED" and failed["error_code"] == "github_unavailable"
        world.fake._failures.clear()
        # The app was uninstalled on GitHub without refactorX hearing about it yet.
        world.fake.installations[INSTALLATION].deleted = True
        lost = await push(stack, world, world.repo.commit("main", {"README.md": "gone\n"}))
        revoked = await wait_review(stack, lost)
        assert revoked["state"] == "FAILED" and revoked["error_code"] == "installation_not_found"
        status = await stack.ok("GET", f"/v1/projects/{project}/git-connection")
        assert status["status"] == "installation_revoked"
        refused = await stack.client.post(f"/v1/projects/{project}/code-reviews", json={})
        assert refused.status_code == 409 and refused.json()["code"] == "installation_revoked"
        uninstall = world.fake.installation_payload(
            "deleted", world.fake.installations[INSTALLATION]
        )
        assert (await deliver(stack, world, "installation", uninstall)).status_code in {200, 202}


async def _validated_fix(stack: Any, finding_id: str) -> dict[str, Any]:
    fix = await stack.ok(
        "POST",
        f"/v1/findings/{finding_id}/fix-proposals",
        json={"recipe_id": "java:string-literal-equals"},
    )
    await stack.ok("POST", f"/v1/fix-proposals/{fix['id']}/validations")
    for _ in range(300):
        current = await stack.ok("GET", f"/v1/fix-proposals/{fix['id']}")
        latest = current["latest_validation"]
        if latest and latest["state"] in {"PASSED", "FAILED", "CANCELED"}:
            assert current["state"] == "VALIDATED", latest
            return dict(current)
        await asyncio.sleep(0.3)
    raise AssertionError("fix validation did not finish")


async def test_fix_pull_requests_forks_and_stale_patches(
    world: World, stack_factory: StackFactory
) -> None:
    async with stack_factory(world.settings) as stack:
        project, review = await connect(stack, world)
        found = await findings(stack, review["head_scan_id"])
        orders = next(
            f
            for f in found
            if f["path"].endswith("shop/Orders.java")
            and f["rule_id"] == "UseEqualsToCompareStrings"
        )
        hidden = next(
            f
            for f in found
            if f["path"].endswith("internal/Hidden.java")
            and f["rule_id"] == "UseEqualsToCompareStrings"
        )
        fix = await _validated_fix(stack, orders["id"])
        assert not fix["pull_request_available"]
        assert "has not allowed" in fix["pull_request_reason"]
        connection = await stack.ok("GET", f"/v1/projects/{project}/git-connection")
        await stack.ok(
            "PATCH",
            f"/v1/projects/{project}/git-connection",
            json={"version": connection["version"], "publish_pull_requests": True},
        )
        fix = await stack.ok("GET", f"/v1/fix-proposals/{fix['id']}")
        assert fix["pull_request_available"] and fix["pull_request_reason"] is None
        opened = await stack.ok("POST", f"/v1/fix-proposals/{fix['id']}/pull-request")
        assert opened["base_ref"] == "main" and opened["base_sha"] == world.main
        assert opened["branch"].startswith("refactorx/fix-")
        again = await stack.ok("POST", f"/v1/fix-proposals/{fix['id']}/pull-request")
        assert again["number"] == opened["number"] and len(world.repo.created_pulls) == 1
        commit = world.repo.commits[opened["commit_sha"]]
        assert commit.parents == [world.main]
        changed = commit.files["src/main/java/shop/Orders.java"].data or b""
        assert b'"PAID".equals(status)' in changed and b'status == "PAID"' not in changed
        untouched = {p: n for p, n in commit.files.items() if p != "src/main/java/shop/Orders.java"}
        assert untouched == {
            p: n for p, n in world.repo.commits[world.main].files.items() if p in untouched
        }
        pull = world.repo.created_pulls[0]
        assert pull["base"] == "main" and "did not compile, build or test" in pull["body"]
        # The branch moved on: a fix for the old commit is stale.
        second = await _validated_fix(stack, hidden["id"])
        world.repo.commit("main", {"README.md": "# Shop\n\nmoved\n"}, "moved")
        stale = await stack.client.post(f"/v1/fix-proposals/{second['id']}/pull-request")
        assert stale.status_code == 409 and stale.json()["code"] == "stale_patch", stale.text
        # Pull requests from forks: not reviewed automatically, never targeted by fix PRs.
        world.fake.add_repo(102, "evil", "shop")
        head = open_pull(world, 11, {"src/main/java/shop/Orders.java": ORDERS_MORE}, fork=102)
        fork_event = world.fake.pull_payload(world.repo, INSTALLATION, "opened", 11)
        ignored = await deliver(stack, world, "pull_request", fork_event)
        assert ignored.json()["outcome"] == "ignored" and "fork" in ignored.json()["detail"]
        manual = await stack.ok(
            "POST",
            f"/v1/projects/{project}/code-reviews",
            json={"kind": "pull_request", "pull_request": 11},
        )
        fork_review = await wait_review(stack, manual["id"])
        assert fork_review["state"] == "SUCCEEDED" and fork_review["fork"]
        assert fork_review["head_sha"] == head
        fork_finding = next(
            f
            for f in await findings(stack, fork_review["head_scan_id"])
            if f["path"].endswith("shop/Orders.java")
            and f["rule_id"] == "UseEqualsToCompareStrings"
        )
        fork_fix = await _validated_fix(stack, fork_finding["id"])
        assert not fork_fix["pull_request_available"] and "fork" in fork_fix["pull_request_reason"]


async def test_push_reviews_and_publication_on_the_lite_profile(world: World) -> None:
    async with lite_stack(world.settings) as stack:
        project, review = await connect(stack, world)
        assert review["state"] == "SUCCEEDED"
        connection = await stack.ok("GET", f"/v1/projects/{project}/git-connection")
        await stack.ok(
            "PATCH",
            f"/v1/projects/{project}/git-connection",
            json={"version": connection["version"], "publish_checks": True},
        )
        change = {"src/main/java/shop/Billing.java": BILLING.replace("DUE", "LATE")}
        after = world.repo.commit("main", change, "late")
        pushed = await wait_review(stack, await push(stack, world, after, world.main))
        assert pushed["state"] == "SUCCEEDED" and pushed["publish_state"] == "published"
        assert pushed["base_sha"] == world.main
        assert pushed["changes"]["modified"]["paths"] == ["src/main/java/shop/Billing.java"]
        assert [r["head_sha"] for r in world.repo.check_runs.values()] == [after]
        # Scheduled reconciliation: a connection whose last full review is too old gets one full
        # review of the default branch (and only one while it is pending).
        engine = create_engine_from_settings(world.settings)
        sessions = create_session_factory(engine)
        try:
            async with transaction(sessions) as session:
                await session.execute(
                    update(GitConnection).values(
                        last_full_review_at=datetime.now(UTC) - timedelta(days=8)
                    )
                )
            started: list[uuid.UUID] = []

            async def start(review_id: uuid.UUID) -> None:
                started.append(review_id)

            queued = await reconcile_due(sessions, start)
            assert queued == started and len(queued) == 1
            assert await reconcile_due(sessions, start) == []
            scheduled = await stack.ok("GET", f"/v1/code-reviews/{queued[0]}")
            assert scheduled["trigger"] == "reconcile" and scheduled["full"]
            assert scheduled["kind"] == "branch" and scheduled["ref"] == "main"
        finally:
            await engine.dispose()
