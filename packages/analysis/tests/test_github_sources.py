"""GitHub sources (P06): app authentication, webhook signatures, commit capture checked against the
commit's tree, change sets and rename-aware finding comparison. The client runs against the
labelled fake GitHub (``crp_devtools.testing.fake_github``); no real GitHub is contacted."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from crp_analysis.manifest import blob_key
from crp_analysis.sources.capture import reconcile_with_tree
from crp_analysis.sources.changes import (
    FindingRef,
    diff_findings,
    diff_manifests,
    impacted_files,
    is_configuration,
)
from crp_analysis.sources.github import (
    GitHubAccessError,
    GitHubClient,
    GitHubNotFoundError,
    GitHubSetup,
    GitHubUnavailableError,
    RepoAccess,
    TreeEntry,
    app_jwt,
    git_blob_id,
    resolve_github,
    verify_signature,
)
from crp_analysis.zip_intake import (
    GitArchiveOptions,
    IntakeLimits,
    IntakeRejectedError,
    process_zip,
)
from crp_core.artifacts import ArtifactKey, FilesystemArtifactStore
from crp_core.config import Settings
from crp_core.domain.states import FileDisposition
from crp_core.local_secrets import write_secret_file
from crp_devtools.testing.fake_github import (
    Executable,
    FakeGitHub,
    FakeRepo,
    Submodule,
    Symlink,
    generate_app_key,
)

LIMITS = IntakeLimits(
    max_archive_bytes=5 * 1024 * 1024,
    max_expanded_bytes=20 * 1024 * 1024,
    max_entries=500,
    max_text_file_bytes=64 * 1024,
    max_compression_ratio=100,
    max_path_length=200,
    max_path_depth=10,
)
LFS_POINTER = (
    b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 123456\n"
)


@pytest.fixture(scope="module")
def app_key() -> tuple[bytes, bytes]:
    return generate_app_key()


@pytest.fixture
def store(tmp_path: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path / "store", max_object_bytes=10 * 1024 * 1024)


# -- signatures and app tokens ---------------------------------------------------------------------


def test_webhook_signatures_are_checked_in_constant_time() -> None:
    secret, body = b"s3cret-hook", b'{"zen":"Keep it logically awesome."}'
    good = FakeGitHub.sign("s3cret-hook", body)
    assert verify_signature(secret, body, good)
    assert not verify_signature(b"other", body, good)
    assert not verify_signature(secret, body + b" ", good)
    assert not verify_signature(secret, body, None)
    assert not verify_signature(secret, body, good.replace("sha256=", "sha1="))


def test_app_jwt_is_rs256_short_lived_and_backdated(app_key: tuple[bytes, bytes]) -> None:
    private, public = app_key
    token = app_jwt("Iv1.client", private, now=1_800_000_000)
    header, payload, signature = token.split(".")

    def decode(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    assert json.loads(decode(header)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(decode(payload))
    assert claims == {"iat": 1_800_000_000 - 60, "exp": 1_800_000_000 + 480, "iss": "Iv1.client"}
    key = serialization.load_pem_public_key(public)
    assert isinstance(key, rsa.RSAPublicKey)
    key.verify(
        decode(signature), f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )


def test_setup_reports_what_is_missing_without_exposing_secrets(
    make_settings: Callable[..., Settings], tmp_path: Path, app_key: tuple[bytes, bytes]
) -> None:
    assert resolve_github(make_settings()).reason == "GitHub is not set up on this server."
    no_key = resolve_github(make_settings(github_app_id=1))
    assert not no_key.app_ready and "PRIVATE_KEY" in (no_key.admin_hint or "")
    bad = resolve_github(make_settings(github_app_id=1, github_private_key="not a key"))
    assert not bad.app_ready and "RSA PEM" in (bad.admin_hint or "")
    key_file = tmp_path / "keys" / "app.pem"
    write_secret_file(key_file, app_key[0].decode())
    ready = resolve_github(
        make_settings(
            github_app_id=1,
            github_client_id="Iv1.x",
            github_client_secret="c" * 40,
            github_app_slug="refactorx-test",
            github_private_key_file=key_file,
            github_webhook_secret="w" * 32,
        )
    )
    assert ready.app_ready and ready.linking_ready and ready.webhooks_ready
    assert ready.issuer == "Iv1.x"
    assert ready.install_url == "https://github.com/apps/refactorx-test/installations/new"
    assert "BEGIN" not in repr(ready) and "c" * 40 not in repr(ready)
    with pytest.raises(ValueError, match="https"):
        make_settings(github_api_url="http://api.github.example.test")


# -- archives ----------------------------------------------------------------------------------------


def _history() -> tuple[FakeRepo, str]:
    repo = FakeRepo(1, "acme", "shop")
    sha = repo.commit(
        "main",
        {
            ".gitattributes": (
                "hidden/*.java export-ignore\nsrc/Version.java export-subst\n*.txt eol=crlf\n"
            ),
            "src/App.java": "class App {}\n",
            "src/Version.java": 'class Version { String id = "$Format:%H$"; }\n',
            "hidden/Backdoor.java": "class Backdoor { void run(String c) throws Exception { Runtime.getRuntime().exec(c); } }\n",
            "notes.txt": "line one\nline two\n",
            "bin/run.sh": Executable(b"#!/bin/sh\necho run\n"),
            "link-to-app": Symlink("src/App.java"),
            "vendor-lib": Submodule("b" * 40),
            "assets/big.bin": LFS_POINTER,
            ".env": "SECRET=1\n",
        },
    )
    return repo, sha


def _capture(repo: FakeRepo, sha: str, store: FilesystemArtifactStore):  # type: ignore[no-untyped-def]
    data = repo.zipball(sha)
    return process_zip(
        io.BytesIO(data),
        archive_bytes=len(data),
        store=store,
        limits=LIMITS,
        git=GitArchiveOptions(),
    )


def test_git_archives_strip_the_root_and_record_symlinks(store: FilesystemArtifactStore) -> None:
    repo, sha = _history()
    outcome = _capture(repo, sha, store)
    paths = {e.path: e for e in outcome.entries}
    assert "src/App.java" in paths and not any(p.startswith("acme-shop-") for p in paths)
    assert paths["link-to-app"].disposition is FileDisposition.EXCLUDED
    assert paths["link-to-app"].reason == "symlink"
    assert "hidden/Backdoor.java" not in paths  # export-ignore: not in GitHub's archive
    assert outcome.executable == {"bin/run.sh"}
    assert outcome.lfs_pointers == {"assets/big.bin"}
    assert outcome.git_blob_ids["src/App.java"] == git_blob_id(b"class App {}\n")
    # Uploads keep rejecting symlinks.
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as archive:
        info = zipfile.ZipInfo("x/link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "target")
    with pytest.raises(IntakeRejectedError) as rejected:
        process_zip(plain, archive_bytes=len(plain.getvalue()), store=store, limits=LIMITS)
    assert rejected.value.code == "symlink_entry"


def test_git_archive_without_a_single_root_is_rejected(store: FilesystemArtifactStore) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a/x.java", "class X {}\n")
        archive.writestr("b/y.java", "class Y {}\n")
    with pytest.raises(IntakeRejectedError) as rejected:
        process_zip(
            buffer,
            archive_bytes=len(buffer.getvalue()),
            store=store,
            limits=LIMITS,
            git=GitArchiveOptions(),
        )
    assert rejected.value.code == "unexpected_archive_layout"


def _tree(repo: FakeRepo, sha: str) -> list[TreeEntry]:
    return [
        TreeEntry(i["path"], i["mode"], i["type"], i["sha"], i.get("size"))
        for i in repo.tree_listing(repo.commits[sha].tree)
    ]


async def test_capture_matches_the_commit_even_when_the_archive_hides_or_alters_files(
    store: FilesystemArtifactStore,
) -> None:
    repo, sha = _history()
    fetched: list[str] = []

    async def fetch(blob: str) -> bytes:
        fetched.append(blob)
        return repo.blobs[blob]

    outcome, report = await reconcile_with_tree(
        _capture(repo, sha, store),
        _tree(repo, sha),
        truncated=False,
        fetch=fetch,
        store=store,
        limits=LIMITS,
        max_fetches=10,
    )
    entries = {e.path: e for e in outcome.entries}
    committed = repo.commits[sha].files
    for path in ("src/App.java", "src/Version.java", "hidden/Backdoor.java", "notes.txt"):
        entry = entries[path]
        assert entry.disposition is FileDisposition.ANALYZABLE, path
        data = store.read_bytes(ArtifactKey(blob_key(entry.sha256 or "")), max_bytes=10**6)
        assert data == committed[path].data, path  # byte-identical to the commit
    assert entries["vendor-lib/"].reason == "submodule"
    assert entries["assets/big.bin"].reason == "git_lfs"
    assert entries["link-to-app"].reason == "symlink"
    assert entries[".env"].reason == "secret_candidate"
    assert report["fetched"] == {"count": 2, "paths": ["hidden/Backdoor.java", "src/Version.java"]}
    assert report["eol_normalized"] == 1  # notes.txt: CRLF undone without a request
    assert report["executable"] == ["bin/run.sh"]
    assert len(fetched) == 2


async def test_capture_limits_fetches_and_says_what_was_left_out(
    store: FilesystemArtifactStore,
) -> None:
    repo, sha = _history()

    async def fetch(blob: str) -> bytes:
        return repo.blobs[blob]

    outcome, report = await reconcile_with_tree(
        _capture(repo, sha, store),
        _tree(repo, sha),
        truncated=False,
        fetch=fetch,
        store=store,
        limits=LIMITS,
        max_fetches=0,
    )
    entries = {e.path: e for e in outcome.entries}
    assert entries["hidden/Backdoor.java"].reason == "not_in_archive"
    assert entries["src/Version.java"].reason == "differs_from_commit"
    assert report["not_in_archive"] == {"count": 1, "paths": ["hidden/Backdoor.java"]}


async def test_capture_refuses_archives_with_files_outside_the_commit(
    store: FilesystemArtifactStore,
) -> None:
    repo, sha = _history()
    tree = [t for t in _tree(repo, sha) if t.path != "src/App.java"]

    async def fetch(blob: str) -> bytes:
        return repo.blobs[blob]

    with pytest.raises(IntakeRejectedError) as rejected:
        await reconcile_with_tree(
            _capture(repo, sha, store),
            tree,
            truncated=False,
            fetch=fetch,
            store=store,
            limits=LIMITS,
            max_fetches=10,
        )
    assert rejected.value.code == "archive_mismatch"


# -- change sets and findings -----------------------------------------------------------------------


def test_change_sets_detect_exact_renames_only_when_unambiguous() -> None:
    base = {"a.java": "1", "b.java": "2", "c.java": "3", "d.java": "4", "e.java": "4"}
    head = {"a.java": "1", "b.java": "9", "moved/c.java": "3", "new.java": "5", "f.java": "4"}
    changes = diff_manifests(base, head)
    assert changes.modified == ("b.java",)
    assert changes.renamed == (("c.java", "moved/c.java"),)
    assert changes.added == ("f.java", "new.java")  # d/e -> f is ambiguous: not a rename
    assert changes.removed == ("d.java", "e.java")
    assert changes.touched == frozenset({"b.java", "moved/c.java", "f.java", "new.java"})
    assert diff_manifests(base, base).empty


def test_configuration_changes_and_impacted_files() -> None:
    assert is_configuration("pom.xml") and is_configuration("web/package-lock.json")
    assert is_configuration("core/resources/core-spring.xml")
    assert is_configuration("force-app/main/default/permissionsets/X.permissionset-meta.xml")
    assert not is_configuration("src/App.java")
    deps = [
        ("src/A.java", "src/B.java"),
        ("src/C.java", "src/B.java"),
        ("src/B.java", "src/D.java"),
    ]
    assert impacted_files({"src/B.java"}, deps) == {"src/A.java", "src/C.java"}


def _finding(fid: str, fp: str, path: str, text: str | None = None) -> FindingRef:
    return FindingRef(fid, fp, "pmd", "UseEqualsToCompareStrings", path, 3, "high", "t", text)


def test_findings_in_renamed_files_are_not_new() -> None:
    base = [
        _finding("b1", "fp-old", "src/Old.java", 'if (s == "x") {'),
        _finding("b2", "fp-kept", "src/Keep.java"),
        _finding("b3", "fp-gone", "src/Fixed.java"),
    ]
    head = [
        _finding("h1", "fp-new-path", "src/New.java", 'if (s == "x") {'),
        _finding("h2", "fp-kept", "src/Keep.java"),
        _finding("h3", "fp-really-new", "src/New.java", 'if (t == "y") {'),
    ]
    result = diff_findings(base, head, {"src/New.java": "src/Old.java"})
    assert [f.id for f in result.new] == ["h3"]
    assert {(h.id, b.id) for h, b in result.unchanged} == {("h1", "b1"), ("h2", "b2")}
    assert [f.id for f in result.absent] == ["b3"]


# -- client against the fake ------------------------------------------------------------------------


@pytest.fixture
def fake(app_key: tuple[bytes, bytes]) -> Iterator[FakeGitHub]:
    github = FakeGitHub(public_key_pem=app_key[1], callback_url="http://127.0.0.1:1/cb").start()
    try:
        yield github
    finally:
        github.stop()


@pytest.fixture
async def client(fake: FakeGitHub, app_key: tuple[bytes, bytes]) -> AsyncIterator[GitHubClient]:
    async def no_sleep(_: float) -> None:
        return None

    setup = GitHubSetup(
        app_ready=True,
        linking_ready=True,
        webhooks_ready=True,
        reason=None,
        admin_hint=None,
        app_id=fake.app_id,
        client_id=fake.client_id,
        slug=fake.slug,
        api_url=fake.base_url,
        web_url=fake.base_url,
        callback_url=fake.callback_url,
        timeout=10,
        _private_key=app_key[0],
        _webhook_secret=b"hook",
        _client_secret=fake.client_secret,
    )
    github = GitHubClient(setup, sleep=no_sleep)
    try:
        yield github
    finally:
        await github.close()


async def test_tokens_are_scoped_to_one_repository_and_the_needed_permissions(
    fake: FakeGitHub, client: GitHubClient, store: FilesystemArtifactStore
) -> None:
    shop, other = fake.add_repo(11, "acme", "shop"), fake.add_repo(12, "acme", "secret")
    sha = shop.commit("main", {"src/App.java": "class App {}\n"})
    other.commit("main", {"README.md": "private\n"})
    fake.add_installation(77, "acme", [11, 12])
    access = RepoAccess(77, 11, "acme/shop")
    assert await client.branch_head(access, "main") == sha
    # A token minted for "shop" cannot read "secret", even through the same installation.
    wrong = RepoAccess(77, 11, "acme/secret")
    with pytest.raises(GitHubNotFoundError):
        await client.branch_head(wrong, "main")
    sink = io.BytesIO()
    size = await client.download_archive(access, sha, sink, max_bytes=10**6)
    assert size == len(sink.getvalue()) > 0
    codeload = [r for r in fake.requests if r["path"].startswith("/_codeload")]
    assert codeload and not any(r["auth"] for r in codeload)  # no credentials to the archive host
    with pytest.raises(Exception, match="size limit"):
        await client.download_archive(access, sha, io.BytesIO(), max_bytes=10)
    fake.installations[77].permissions.pop("checks")
    with pytest.raises(GitHubAccessError) as denied:
        await client.create_check_run(access, {"name": "x", "head_sha": sha})
    assert denied.value.code == "permission_not_granted"


async def test_outages_are_retried_then_reported(fake: FakeGitHub, client: GitHubClient) -> None:
    repo = fake.add_repo(21, "acme", "api")
    sha = repo.commit("main", {"a.js": "let a = 1;\n"})
    fake.add_installation(88, "acme", [21])
    access = RepoAccess(88, 21, "acme/api")
    fake.fail("/repos/acme/api/git/ref", 503, times=2)
    assert await client.branch_head(access, "main") == sha  # two retries
    fake.fail("/repos/acme/api/git/ref", 502, times=3)
    with pytest.raises(GitHubUnavailableError):
        await client.branch_head(access, "main")


async def test_revoked_and_suspended_installations(fake: FakeGitHub, client: GitHubClient) -> None:
    fake.add_repo(31, "acme", "web")
    installation = fake.add_installation(99, "acme", [31])
    access = RepoAccess(99, 31, "acme/web")
    installation.suspended = True
    with pytest.raises(GitHubAccessError) as suspended:
        await client.repository(access)
    assert suspended.value.code == "installation_suspended"
    installation.suspended, installation.deleted = False, True
    with pytest.raises(GitHubAccessError) as deleted:
        await client.repository(access)
    assert deleted.value.code == "installation_not_found"


async def test_linking_uses_github_sign_in_and_lists_only_accessible_installations(
    fake: FakeGitHub, client: GitHubClient
) -> None:
    for number in range(150):  # more than one page
        fake.add_repo(1000 + number, "acme", f"repo{number}")
    fake.add_installation(55, "acme", [1000 + n for n in range(150)])
    fake.add_installation(56, "someone-else", [])
    fake.add_user("octo", [55])
    url = client.authorize_url("state-123")
    assert "state=state-123" in url and "redirect_uri=" in url
    code_redirect = fake.handle("GET", url.removeprefix(fake.base_url), {}, b"")
    location = code_redirect[1]["Location"]
    assert location.startswith("http://127.0.0.1:1/cb?") and "state=state-123" in location
    code = location.split("code=")[1].split("&")[0]
    user_token = await client.exchange_code(code)
    installations = await client.user_installations(user_token)
    assert [i["id"] for i in installations] == [55]
    with pytest.raises(GitHubAccessError):
        await client.exchange_code(code)  # codes work once
    repos = await client.installation_repositories(55)
    assert len(repos) == 150


def test_blob_ids_match_git() -> None:
    # `printf 'hello\n' | git hash-object --stdin`
    assert git_blob_id(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"
    assert hashlib.sha256(b"x").hexdigest() != git_blob_id(b"x")


# -- publication --------------------------------------------------------------------------------


def _result(state: str, *items: tuple[str, str]) -> dict[str, object]:
    new = [
        {
            "severity": severity,
            "title": f"[x](javascript:alert(1)) {n}",
            "path": path,
            "line": n + 1,
        }
        for n, (severity, path) in enumerate(items)
    ]
    return {"state": state, "new": len(new), "new_items": new, "by_severity": {}}


def test_check_conclusions_never_overstate() -> None:
    from crp_analysis.sources.publication import conclusion

    assert conclusion(_result("SUCCEEDED"), "high") == "success"
    assert conclusion(_result("SUCCEEDED", ("medium", "a.java")), "high") == "neutral"
    assert conclusion(_result("SUCCEEDED", ("high", "a.java")), "high") == "failure"
    assert conclusion(_result("SUCCEEDED", ("critical", "a.java")), "never") == "neutral"
    assert conclusion(_result("PARTIAL"), "high") == "neutral"  # incomplete: never success
    assert conclusion(_result("FAILED"), "high") == "neutral"


def test_published_text_escapes_repository_content_and_caps_annotations() -> None:
    from crp_analysis.sources.publication import check_run_payload, comment_body

    items = [("medium", f"src/<b>|{n}.java") for n in range(60)]
    payload = check_run_payload(
        {"review_id": "r1", "head_sha": "a" * 40, "pr_number": 4},
        _result("SUCCEEDED", *items),
        "never",
        None,
    )
    assert len(payload["output"]["annotations"]) == 50
    summary = payload["output"]["summary"]
    assert "[x](javascript" not in summary and "\\[x\\]\\(javascript" in summary
    assert "<b>" not in summary and "\\|" in summary
    assert "nothing was built, run or deployed" in summary
    body = comment_body(
        {"review_id": "r1", "head_sha": "a" * 40, "pr_number": 4}, _result("SUCCEEDED"), None
    )
    assert (
        body.startswith("<!-- refactorx:review -->")
        and "No new problems in this pull request" in body
    )
