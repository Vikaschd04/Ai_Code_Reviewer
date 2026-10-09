"""Labelled fake GitHub for tests (REST subset, web sign-in, archives). Never use it for real work.

It models what refactorX relies on, closely enough that the real client and worker code run
unchanged:
- **Git objects:** real Git object ids for blobs, trees and commits.
- **Archives:** built like ``git archive``. ``export-ignore``, ``export-subst`` and ``eol=crlf``
  from the root ``.gitattributes`` are honoured; symlinks, submodules and Git LFS pointers behave as
  on GitHub.
- **App authentication:** RS256 JWT verified with the app's public key.
- **Tokens:** installation tokens limited to repositories and permissions, and enforced on every
  endpoint.
- **Linking:** the OAuth web flow and ``/user/installations``.
- **Publication:** check runs, issue comments, Git data writes and pull requests. Every write is
  recorded so tests can assert it.

Failures can be injected: outages, revoked installations, suspended apps. It listens on loopback
only. Browser tests drive it through ``/_fake/*`` control endpoints:
- create a commit;
- open a pull request;
- deliver a signed webhook;
- read what refactorX posted.
These endpoints exist only in this double.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import hmac
import io
import json
import secrets
import threading
import time
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from crp_devtools.infra import LOOPBACK

LABEL = "[Test GitHub]"
_AUTHOR = "Fake Author <author@example.test> 1767225600 +0000"
LFS_PREFIX = b"version https://git-lfs.github.com/spec/v1"
_LEVEL = {"read": 1, "write": 2}


def generate_app_key() -> tuple[bytes, bytes]:
    """A fresh RSA key pair for a test app: (private PEM, public PEM)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public


# -- Git object model ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Symlink:
    target: str


@dataclass(frozen=True)
class Submodule:
    sha: str


@dataclass(frozen=True)
class Executable:
    data: bytes


FileSpec = bytes | str | Symlink | Submodule | Executable


def _sha1(kind: str, body: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(body)}\0".encode() + body, usedforsecurity=False).hexdigest()


@dataclass
class _Node:
    mode: str  # 100644, 100755, 120000, 160000, 040000
    sha: str
    data: bytes | None = None


@dataclass
class _Commit:
    sha: str
    tree: str
    parents: list[str]
    message: str
    files: dict[str, _Node]  # flattened path -> node (blobs, symlinks, submodules)


@dataclass
class FakePull:
    number: int
    head_ref: str
    head_sha: str
    base_ref: str
    title: str
    author: str
    state: str = "open"
    head_repo_id: int | None = None  # None = same repository; another id = a fork
    draft: bool = False


@dataclass
class FakeRepo:
    id: int
    owner: str
    name: str
    default_branch: str = "main"
    private: bool = True
    truncate_trees: bool = False
    lfs_in_archives: bool = False
    lfs_objects: dict[str, bytes] = field(default_factory=dict)
    blobs: dict[str, bytes] = field(default_factory=dict)
    trees: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    commits: dict[str, _Commit] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)
    pulls: dict[int, FakePull] = field(default_factory=dict)
    check_runs: dict[int, dict[str, Any]] = field(default_factory=dict)
    comments: dict[int, dict[str, Any]] = field(default_factory=dict)
    created_pulls: list[dict[str, Any]] = field(default_factory=list)
    created_commits: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "full_name": self.full_name,
            "private": self.private,
            "archived": False,
            "default_branch": self.default_branch,
            "owner": {"login": self.owner},
            "html_url": f"https://github.example.test/{self.full_name}",
        }

    # -- building history ----------------------------------------------------------------------

    def _write_tree(self, files: dict[str, _Node], prefix: str = "") -> str:
        children: dict[str, dict[str, _Node]] = {}
        entries: list[tuple[str, str, str]] = []  # (name, mode, sha)
        for path, node in files.items():
            head, _, rest = path.partition("/")
            if rest:
                children.setdefault(head, {})[rest] = node
            else:
                entries.append((head, node.mode, node.sha))
        for name, sub in children.items():
            entries.append((name, "040000", self._write_tree(sub, f"{prefix}{name}/")))
        entries.sort(key=lambda e: e[0] + ("/" if e[1] == "040000" else ""))
        body = b"".join(
            f"{mode.lstrip('0')} {name}\0".encode() + bytes.fromhex(sha)
            for name, mode, sha in entries
        )
        sha = _sha1("tree", body)
        self.trees[sha] = [
            {"name": name, "mode": mode, "sha": sha_, "prefix": prefix}
            for name, mode, sha_ in entries
        ]
        return sha

    def commit(
        self,
        branch: str,
        changes: dict[str, FileSpec | None],
        message: str = "change",
        *,
        parents: list[str] | None = None,
        base: str | None = None,
    ) -> str:
        """Commit ``changes`` (None deletes a path) on top of ``base`` (default: the branch)."""
        start = base if base is not None else self.branches.get(branch)
        files = dict(self.commits[start].files) if start else {}
        for path, spec in changes.items():
            if spec is None:
                files.pop(path, None)
            elif isinstance(spec, Symlink):
                data = spec.target.encode()
                files[path] = _Node("120000", self._blob(data), data)
            elif isinstance(spec, Submodule):
                files[path] = _Node("160000", spec.sha)
            elif isinstance(spec, Executable):
                files[path] = _Node("100755", self._blob(spec.data), spec.data)
            else:
                data = spec.encode() if isinstance(spec, str) else spec
                files[path] = _Node("100644", self._blob(data), data)
        tree = self._write_tree(files)
        parent_list = parents if parents is not None else ([start] if start else [])
        body = (
            f"tree {tree}\n"
            + "".join(f"parent {p}\n" for p in parent_list)
            + f"author {_AUTHOR}\ncommitter {_AUTHOR}\n\n{message}\n"
        ).encode()
        sha = _sha1("commit", body)
        self.commits[sha] = _Commit(sha, tree, parent_list, message, files)
        self.branches[branch] = sha
        return sha

    def _blob(self, data: bytes) -> str:
        sha = _sha1("blob", data)
        self.blobs[sha] = data
        return sha

    def ancestors(self, sha: str) -> list[str]:
        order, seen, queue = [], set(), [sha]
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            order.append(current)
            queue.extend(self.commits[current].parents)
        return order

    def merge_base(self, base: str, head: str) -> str:
        head_ancestors = set(self.ancestors(head))
        return next(c for c in self.ancestors(base) if c in head_ancestors)

    def tree_listing(self, tree_sha: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        def walk(sha: str, prefix: str) -> None:
            for entry in self.trees[sha]:
                path = f"{prefix}{entry['name']}"
                if entry["mode"] == "040000":
                    items.append(
                        {"path": path, "mode": "040000", "type": "tree", "sha": entry["sha"]}
                    )
                    walk(entry["sha"], f"{path}/")
                elif entry["mode"] == "160000":
                    items.append(
                        {"path": path, "mode": "160000", "type": "commit", "sha": entry["sha"]}
                    )
                else:
                    size = len(self.blobs[entry["sha"]])
                    items.append(
                        {
                            "path": path,
                            "mode": entry["mode"],
                            "type": "blob",
                            "sha": entry["sha"],
                            "size": size,
                        }
                    )

        walk(tree_sha, "")
        return items

    # -- archives (git archive semantics) -------------------------------------------------------

    def _attributes(self, files: dict[str, _Node]) -> list[tuple[str, set[str]]]:
        node = files.get(".gitattributes")
        if node is None or node.data is None:
            return []
        rules = []
        for line in node.data.decode().splitlines():
            parts = line.split()
            if len(parts) >= 2 and not parts[0].startswith("#"):
                rules.append((parts[0], set(parts[1:])))
        return rules

    @staticmethod
    def _has(rules: list[tuple[str, set[str]]], path: str, attribute: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        return any(
            attribute in attributes
            and (fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern))
            for pattern, attributes in rules
        )

    def zipball(self, sha: str) -> bytes:
        commit = self.commits[sha]
        rules = self._attributes(commit.files)
        root = f"{self.owner}-{self.name}-{sha[:7]}/"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.comment = sha.encode()
            dirs = {root}
            for path in sorted(commit.files):
                node = commit.files[path]
                if self._has(rules, path, "export-ignore"):
                    continue
                parts = path.split("/")
                for depth in range(1, len(parts)):
                    dirs.add(root + "/".join(parts[:depth]) + "/")
            for directory in sorted(dirs):
                info = zipfile.ZipInfo(directory, date_time=(2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o40755 << 16) | 0x10
                archive.writestr(info, b"")
            for path in sorted(commit.files):
                node = commit.files[path]
                if self._has(rules, path, "export-ignore"):
                    continue
                if node.mode == "160000":
                    info = zipfile.ZipInfo(f"{root}{path}/", date_time=(2026, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = (0o40755 << 16) | 0x10
                    archive.writestr(info, b"")
                    continue
                data = node.data or b""
                if data.startswith(LFS_PREFIX) and self.lfs_in_archives:
                    oid = data.split(b"oid sha256:", 1)[1].split(b"\n", 1)[0].decode()
                    data = self.lfs_objects.get(oid, data)
                if self._has(rules, path, "export-subst"):
                    data = data.replace(b"$Format:%H$", sha.encode())
                if self._has(rules, path, "eol=crlf") and b"\0" not in data:
                    data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                info = zipfile.ZipInfo(root + path, date_time=(2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                mode = {"100755": 0o100755, "120000": 0o120777}.get(node.mode, 0o100644)
                info.external_attr = mode << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        return buffer.getvalue()


@dataclass
class FakeInstallation:
    id: int
    account: str
    repo_ids: list[int]
    permissions: dict[str, str] = field(
        default_factory=lambda: {
            "contents": "write",
            "metadata": "read",
            "pull_requests": "write",
            "checks": "write",
        }
    )
    suspended: bool = False
    deleted: bool = False

    def as_json(self, app_id: int) -> dict[str, Any]:
        return {
            "id": self.id,
            "app_id": app_id,
            "account": {"login": self.account, "type": "Organization", "id": 1000 + self.id},
            "repository_selection": "selected",
            "permissions": self.permissions,
            "events": ["push", "pull_request"],
            "suspended_at": "2026-01-01T00:00:00Z" if self.suspended else None,
        }


@dataclass
class _IssuedToken:
    installation: int | None
    repo_ids: list[int] | None  # None = every repository of the installation
    permissions: dict[str, str]
    expires: float
    user: str | None = None


class FakeGitHub:
    """Thread-safe fake; configure repos/installations/users, then ``start()``."""

    def __init__(
        self,
        *,
        app_id: int = 4242,
        client_id: str = "Iv1.faketestclient",
        client_secret: str | None = None,
        slug: str = "refactorx-test",
        public_key_pem: bytes,
        callback_url: str | None = None,
    ) -> None:
        self.app_id = app_id
        self.client_id = client_id
        self.client_secret = client_secret or secrets.token_hex(20)
        self.slug = slug
        self.callback_url = callback_url
        public_key = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise TypeError("the test app key must be RSA")
        self._public_key = public_key
        self.repos: dict[int, FakeRepo] = {}
        self.installations: dict[int, FakeInstallation] = {}
        self.users: dict[str, list[int]] = {}  # login -> installation ids the user can access
        self.signed_in_user: str | None = None  # who "approves" the next authorization
        self._codes: dict[str, str] = {}
        self._tokens: dict[str, _IssuedToken] = {}
        self._archive_links: dict[str, tuple[int, str, float]] = {}
        self.requests: list[dict[str, Any]] = []
        self._failures: list[tuple[str, int]] = []  # (path prefix, status)
        self._next_ids = {"check": 7000, "comment": 9000, "pull": 500}
        self._pending_tree_files: dict[str, dict[str, _Node]] = {}
        self.lock = threading.RLock()
        self.webhook_url: str | None = None  # where /_fake/deliver posts (browser tests)
        self.webhook_secret: str | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- configuration -------------------------------------------------------------------------

    def add_repo(self, repo_id: int, owner: str, name: str, **kwargs: Any) -> FakeRepo:
        repo = FakeRepo(repo_id, owner, name, **kwargs)
        self.repos[repo_id] = repo
        return repo

    def add_installation(
        self, installation_id: int, account: str, repo_ids: list[int]
    ) -> FakeInstallation:
        installation = FakeInstallation(installation_id, account, repo_ids)
        self.installations[installation_id] = installation
        return installation

    def add_user(self, login: str, installation_ids: list[int]) -> None:
        self.users[login] = installation_ids
        self.signed_in_user = self.signed_in_user or login

    def fail(self, path_prefix: str, status: int = 503, times: int = 1) -> None:
        with self.lock:
            self._failures.extend([(path_prefix, status)] * times)

    def repo_by_name(self, full_name: str) -> FakeRepo:
        return next(r for r in self.repos.values() if r.full_name == full_name)

    # -- server ---------------------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("fake GitHub is not running")
        port = self._server.server_address[1]
        return f"http://{LOOPBACK}:{port}"

    def start(self) -> FakeGitHub:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _dispatch(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                status, headers, payload = fake.handle(method, self.path, dict(self.headers), body)
                data = (
                    payload
                    if isinstance(payload, bytes)
                    else json.dumps(payload).encode()
                    if payload is not None
                    else b""
                )
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                if "Content-Type" not in headers:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_POST(self) -> None:
                self._dispatch("POST")

            def do_PATCH(self) -> None:
                self._dispatch("PATCH")

        self._server = ThreadingHTTPServer((LOOPBACK, 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # -- request handling ---------------------------------------------------------------------

    def handle(
        self, method: str, raw_path: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], Any]:
        parts = urlsplit(raw_path)
        path, query = parts.path, parse_qs(parts.query)
        headers = {key.lower(): value for key, value in headers.items()}
        auth = headers.get("authorization")
        if path == "/_fake/deliver" and method == "POST":
            return self._control_deliver(json.loads(body or b"{}"))
        with self.lock:
            self.requests.append({"method": method, "path": path, "auth": auth is not None})
            for index, (prefix, status) in enumerate(self._failures):
                if path.startswith(prefix):
                    del self._failures[index]
                    return status, {}, {"message": f"{LABEL} injected failure"}
            try:
                return self._route(method, path, query, auth, body, headers)
            except _Reply as reply:
                return reply.status, reply.headers, reply.payload

    def _route(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        auth: str | None,
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, str], Any]:
        segments = [unquote(s) for s in path.strip("/").split("/")]
        json_body = headers.get("content-type", "").startswith("application/json")
        data = json.loads(body) if body and json_body else None
        # web: authorization and token exchange
        if path == "/login/oauth/authorize":
            return self._authorize(query)
        if path == "/login/oauth/access_token" and method == "POST":
            return self._exchange(parse_qs(body.decode()))
        if segments[:1] == ["apps"] and segments[2:] == ["installations", "new"]:
            html = f"<html><body>{LABEL} Install {self.slug}</body></html>".encode()
            return 200, {"Content-Type": "text/html"}, html
        if segments[:1] == ["_codeload"]:
            return self._codeload(segments, query)
        if segments[:1] == ["_fake"]:
            return self._control(method, segments[1:], query, data or {})
        # app (JWT)
        if segments[:2] == ["app", "installations"]:
            self._verify_jwt(auth)
            installation = self._installation(int(segments[2]))
            if len(segments) == 3 and method == "GET":
                return 200, {}, installation.as_json(self.app_id)
            if segments[3:] == ["access_tokens"] and method == "POST":
                return self._mint(installation, data or {})
        if path == "/user/installations":
            token = self._token(auth)
            if token.user is None:
                raise _Reply(403, {"message": "user token required"})
            visible = [
                self.installations[i].as_json(self.app_id)
                for i in self.users.get(token.user, [])
                if i in self.installations and not self.installations[i].deleted
            ]
            return 200, {}, {"total_count": len(visible), "installations": visible}
        if path == "/installation/repositories":
            token = self._token(auth)
            installation = self._installation(token.installation or 0)
            repos = [self.repos[r].as_json() for r in installation.repo_ids if r in self.repos]
            return self._page(repos, query, "repositories", path)
        if segments[:1] == ["repositories"]:
            repo = self.repos.get(int(segments[1]))
            if repo is None:
                raise _Reply(404, {"message": "Not Found"})
            self._need(auth, repo, "metadata", "read")
            return 200, {}, repo.as_json()
        if segments[:1] == ["repos"] and len(segments) >= 4:
            repo = self._repo(segments[1], segments[2])
            return self._repo_route(method, repo, segments[3:], query, auth, data)
        return 404, {}, {"message": "Not Found"}

    def _repo_route(
        self,
        method: str,
        repo: FakeRepo,
        rest: list[str],
        query: dict[str, list[str]],
        auth: str | None,
        data: Any,
    ) -> tuple[int, dict[str, str], Any]:
        if method == "GET":
            if rest[:3] == ["git", "ref", "heads"]:
                self._need(auth, repo, "contents", "read")
                branch = "/".join(rest[3:])
                if branch not in repo.branches:
                    raise _Reply(404, {"message": "Not Found"})
                sha = repo.branches[branch]
                return (
                    200,
                    {},
                    {"ref": f"refs/heads/{branch}", "object": {"sha": sha, "type": "commit"}},
                )
            if rest[:1] == ["pulls"] and len(rest) == 2:
                self._need(auth, repo, "pull_requests", "read")
                pull = repo.pulls.get(int(rest[1]))
                if pull is None:
                    raise _Reply(404, {"message": "Not Found"})
                return 200, {}, self._pull_json(repo, pull)
            if rest[:1] == ["compare"]:
                self._need(auth, repo, "contents", "read")
                base, _, head = rest[1].partition("...")
                base_sha = repo.branches.get(base, base)
                head_sha = repo.branches.get(head, head)
                if base_sha not in repo.commits or head_sha not in repo.commits:
                    raise _Reply(404, {"message": "Not Found"})
                merge = repo.merge_base(base_sha, head_sha)
                return 200, {}, {"merge_base_commit": {"sha": merge}, "status": "diverged"}
            if rest[:2] == ["git", "commits"]:
                self._need(auth, repo, "contents", "read")
                commit = repo.commits.get(rest[2])
                if commit is None:
                    raise _Reply(404, {"message": "Not Found"})
                return (
                    200,
                    {},
                    {
                        "sha": commit.sha,
                        "tree": {"sha": commit.tree},
                        "parents": [{"sha": p} for p in commit.parents],
                    },
                )
            if rest[:2] == ["git", "trees"]:
                self._need(auth, repo, "contents", "read")
                if rest[2] not in repo.trees:
                    raise _Reply(404, {"message": "Not Found"})
                items = repo.tree_listing(rest[2])
                if repo.truncate_trees:
                    items = items[: max(1, len(items) // 2)]
                return 200, {}, {"sha": rest[2], "tree": items, "truncated": repo.truncate_trees}
            if rest[:2] == ["git", "blobs"]:
                self._need(auth, repo, "contents", "read")
                blob = repo.blobs.get(rest[2])
                if blob is None:
                    raise _Reply(404, {"message": "Not Found"})
                return (
                    200,
                    {},
                    {
                        "sha": rest[2],
                        "size": len(blob),
                        "encoding": "base64",
                        "content": base64.b64encode(blob).decode(),
                    },
                )
            if rest[:1] == ["zipball"]:
                self._need(auth, repo, "contents", "read")
                sha = repo.branches.get(rest[1], rest[1])
                if sha not in repo.commits:
                    raise _Reply(404, {"message": "Not Found"})
                link = secrets.token_hex(12)
                self._archive_links[link] = (repo.id, sha, time.time() + 300)
                return 302, {"Location": f"{self.base_url}/_codeload/{link}"}, None
        if method == "POST":
            if rest == ["check-runs"]:
                self._need(auth, repo, "checks", "write")
                check_id = self._new_id("check")
                repo.check_runs[check_id] = {**data, "id": check_id, "updates": 0}
                return 201, {}, {"id": check_id}
            if rest[:1] == ["issues"] and rest[2:] == ["comments"]:
                self._need(auth, repo, "pull_requests", "write")
                comment_id = self._new_id("comment")
                repo.comments[comment_id] = {
                    "id": comment_id,
                    "number": int(rest[1]),
                    "body": data["body"],
                    "updates": 0,
                }
                return 201, {}, {"id": comment_id}
            if rest == ["git", "blobs"]:
                self._need(auth, repo, "contents", "write")
                content = (
                    base64.b64decode(data["content"])
                    if data.get("encoding") == "base64"
                    else data["content"].encode()
                )
                return 201, {}, {"sha": repo._blob(content)}
            if rest == ["git", "trees"]:
                self._need(auth, repo, "contents", "write")
                return 201, {}, {"sha": self._tree_with(repo, data)}
            if rest == ["git", "commits"]:
                self._need(auth, repo, "contents", "write")
                parent = data["parents"][0]
                files = self._pending_tree_files.pop(data["tree"])
                changes: dict[str, FileSpec | None] = {}
                for path, node in files.items():
                    if repo.commits[parent].files.get(path) != node:
                        changes[path] = (
                            Executable(node.data or b"")
                            if node.mode == "100755"
                            else (node.data or b"")
                        )
                for path in repo.commits[parent].files.keys() - files.keys():
                    changes[path] = None  # deleted in the new tree
                sha = repo.commit("__fix_tmp__", changes, data["message"], base=parent)
                del repo.branches["__fix_tmp__"]
                repo.created_commits.append(sha)
                return 201, {}, {"sha": sha}
            if rest == ["git", "refs"]:
                self._need(auth, repo, "contents", "write")
                name = str(data["ref"]).removeprefix("refs/heads/")
                if name in repo.branches:
                    raise _Reply(422, {"message": "Reference already exists"})
                repo.branches[name] = data["sha"]
                return 201, {}, {"ref": data["ref"], "object": {"sha": data["sha"]}}
            if rest == ["pulls"]:
                self._need(auth, repo, "pull_requests", "write")
                number = self._new_id("pull")
                head_sha = repo.branches[data["head"]]
                pull = FakePull(
                    number, data["head"], head_sha, data["base"], data["title"], "refactorx[bot]"
                )
                repo.pulls[number] = pull
                repo.created_pulls.append({**data, "number": number})
                return 201, {}, self._pull_json(repo, pull)
        if method == "PATCH":
            if rest[:1] == ["check-runs"]:
                self._need(auth, repo, "checks", "write")
                run = repo.check_runs.get(int(rest[1]))
                if run is None:
                    raise _Reply(404, {"message": "Not Found"})
                run.update(data)
                run["updates"] += 1
                return 200, {}, {"id": run["id"]}
            if rest[:2] == ["issues", "comments"]:
                self._need(auth, repo, "pull_requests", "write")
                comment = repo.comments.get(int(rest[2]))
                if comment is None:
                    raise _Reply(404, {"message": "Not Found"})
                comment["body"] = data["body"]
                comment["updates"] += 1
                return 200, {}, {"id": comment["id"]}
        return 404, {}, {"message": "Not Found"}

    def _tree_with(self, repo: FakeRepo, data: dict[str, Any]) -> str:
        base = next(c for c in repo.commits.values() if c.tree == data["base_tree"])
        files = dict(base.files)
        for item in data["tree"]:
            if item["sha"] is None:  # GitHub: a null sha deletes the path
                files.pop(item["path"], None)
            else:
                files[item["path"]] = _Node(item["mode"], item["sha"], repo.blobs[item["sha"]])
        sha = repo._write_tree(files)
        self._pending_tree_files[sha] = files
        return sha

    def _pull_json(self, repo: FakeRepo, pull: FakePull) -> dict[str, Any]:
        head_repo = self.repos.get(pull.head_repo_id) if pull.head_repo_id else repo
        return {
            "number": pull.number,
            "state": "closed" if pull.state in {"closed", "merged"} else "open",
            "merged": pull.state == "merged",
            "title": pull.title,
            "draft": pull.draft,
            "user": {"login": pull.author},
            "html_url": f"https://github.example.test/{repo.full_name}/pull/{pull.number}",
            "head": {
                "sha": pull.head_sha,
                "ref": pull.head_ref,
                "repo": {
                    "id": pull.head_repo_id or repo.id,
                    "full_name": head_repo.full_name if head_repo else "fork/unknown",
                },
            },
            "base": {
                "sha": repo.branches[pull.base_ref],
                "ref": pull.base_ref,
                "repo": {"id": repo.id, "full_name": repo.full_name},
            },
        }

    def _codeload(
        self, segments: list[str], query: dict[str, list[str]]
    ) -> tuple[int, dict[str, str], Any]:
        link = self._archive_links.get(segments[1] if len(segments) > 1 else "")
        if link is None or link[2] < time.time():
            raise _Reply(404, {"message": "Not Found"})
        repo = self.repos[link[0]]
        return 200, {"Content-Type": "application/zip"}, repo.zipball(link[1])

    def _page(
        self, items: list[Any], query: dict[str, list[str]], key: str, path: str
    ) -> tuple[int, dict[str, str], Any]:
        per_page = int((query.get("per_page") or ["30"])[0])
        page = int((query.get("page") or ["1"])[0])
        chunk = items[(page - 1) * per_page : page * per_page]
        headers = {}
        if page * per_page < len(items):
            headers["Link"] = (
                f'<{self.base_url}{path}?per_page={per_page}&page={page + 1}>; rel="next"'
            )
        return 200, headers, {"total_count": len(items), key: chunk}

    # -- authentication ---------------------------------------------------------------------------

    def _verify_jwt(self, auth: str | None) -> None:
        if not auth or not auth.startswith("Bearer "):
            raise _Reply(401, {"message": "A JSON web token could not be decoded"})
        token = auth.removeprefix("Bearer ")
        try:
            header, payload, signature = token.split(".")
            signed = f"{header}.{payload}".encode()
            raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            self._public_key.verify(raw, signed, padding.PKCS1v15(), hashes.SHA256())
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (ValueError, InvalidSignature) as exc:
            raise _Reply(401, {"message": "A JSON web token could not be decoded"}) from exc
        now = time.time()
        if claims.get("iss") not in {self.client_id, str(self.app_id)}:
            raise _Reply(401, {"message": "'Issuer' claim ('iss') must be the app"})
        if (
            not (claims["iat"] <= now + 60 < claims["exp"] + 60)
            or claims["exp"] - claims["iat"] > 600
        ):
            raise _Reply(
                401, {"message": "'Expiration time' claim ('exp') is too far in the future"}
            )

    def _installation(self, installation_id: int) -> FakeInstallation:
        installation = self.installations.get(installation_id)
        if installation is None or installation.deleted:
            raise _Reply(404, {"message": "Not Found"})
        return installation

    def _mint(
        self, installation: FakeInstallation, body: dict[str, Any]
    ) -> tuple[int, dict[str, str], Any]:
        if installation.suspended:
            raise _Reply(403, {"message": "This installation has been suspended"})
        requested = body.get("permissions") or dict(installation.permissions)
        for name, level in requested.items():
            granted = installation.permissions.get(name)
            if granted is None or _LEVEL[granted] < _LEVEL[level]:
                raise _Reply(
                    422, {"message": f"The permissions requested are not granted ({name})"}
                )
        repo_ids = body.get("repository_ids")
        if repo_ids is not None and not set(repo_ids) <= set(installation.repo_ids):
            raise _Reply(
                422,
                {"message": "A repository does not exist or is not accessible"},
            )
        token = "ghs_" + secrets.token_hex(18)
        self._tokens[token] = _IssuedToken(installation.id, repo_ids, requested, time.time() + 3600)
        expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        return 201, {}, {"token": token, "expires_at": expires, "permissions": requested}

    def _token(self, auth: str | None) -> _IssuedToken:
        value = (auth or "").removeprefix("Bearer ").removeprefix("token ")
        token = self._tokens.get(value)
        if token is None or token.expires < time.time():
            raise _Reply(401, {"message": "Bad credentials"})
        if token.installation is not None:
            installation = self.installations.get(token.installation)
            if installation is None or installation.deleted or installation.suspended:
                raise _Reply(401, {"message": "Bad credentials"})
        return token

    def _need(self, auth: str | None, repo: FakeRepo, permission: str, level: str) -> None:
        token = self._token(auth)
        installation = self.installations[token.installation or 0]
        allowed = token.repo_ids if token.repo_ids is not None else installation.repo_ids
        if repo.id not in allowed or repo.id not in installation.repo_ids:
            raise _Reply(404, {"message": "Not Found"})
        granted = token.permissions.get(permission)
        if permission == "metadata" and granted is None and token.permissions:
            granted = "read"
        if granted is None or _LEVEL[granted] < _LEVEL[level]:
            raise _Reply(403, {"message": "Resource not accessible by integration"})

    def _repo(self, owner: str, name: str) -> FakeRepo:
        for repo in self.repos.values():
            if repo.owner == owner and repo.name == name:
                return repo
        raise _Reply(404, {"message": "Not Found"})

    def _new_id(self, kind: str) -> int:
        self._next_ids[kind] += 1
        return self._next_ids[kind]

    # -- web authorization --------------------------------------------------------------------

    def _authorize(self, query: dict[str, list[str]]) -> tuple[int, dict[str, str], Any]:
        if (query.get("client_id") or [""])[0] != self.client_id:
            return 404, {}, {"message": "unknown client"}
        state = (query.get("state") or [""])[0]
        target = (query.get("redirect_uri") or [self.callback_url or ""])[0]
        if self.callback_url and target != self.callback_url:
            return 400, {}, {"message": "redirect_uri mismatch"}
        user = self.signed_in_user
        if user is None:
            return 403, {}, {"message": "nobody is signed in to the fake"}
        code = secrets.token_hex(10)
        self._codes[code] = user
        return 302, {"Location": f"{target}?{urlencode({'code': code, 'state': state})}"}, None

    def _exchange(self, form: dict[str, list[str]]) -> tuple[int, dict[str, str], Any]:
        def value(key: str) -> str:
            return (form.get(key) or [""])[0]

        if value("client_id") != self.client_id or not hmac.compare_digest(
            value("client_secret"), self.client_secret
        ):
            return 200, {}, {"error": "incorrect_client_credentials"}
        user = self._codes.pop(value("code"), None)
        if user is None:
            return (
                200,
                {},
                {
                    "error": "bad_verification_code",
                    "error_description": "The code is incorrect or expired.",
                },
            )
        token = "ghu_" + secrets.token_hex(18)
        self._tokens[token] = _IssuedToken(None, None, {}, time.time() + 28800, user=user)
        return 200, {}, {"access_token": token, "token_type": "bearer", "expires_in": 28800}

    # -- test control (browser tests only) -----------------------------------------------------

    def _control(
        self, method: str, rest: list[str], query: dict[str, list[str]], data: dict[str, Any]
    ) -> tuple[int, dict[str, str], Any]:
        if method == "GET" and rest == ["state"]:
            repo = self.repo_by_name((query.get("repo") or [""])[0])
            return (
                200,
                {},
                {
                    "check_runs": list(repo.check_runs.values()),
                    "comments": list(repo.comments.values()),
                    "pulls": repo.created_pulls,
                    "branches": repo.branches,
                },
            )
        if method == "POST" and rest == ["commit"]:
            repo = self.repo_by_name(str(data["repo"]))
            files: dict[str, FileSpec | None] = {
                path: (None if content is None else str(content))
                for path, content in dict(data["files"]).items()
            }
            base = data.get("base")
            sha = repo.commit(
                str(data["branch"]),
                files,
                str(data.get("message") or "change"),
                base=repo.branches.get(str(base), base) if base else None,
            )
            return 201, {}, {"sha": sha}
        if method == "POST" and rest == ["pull"]:
            repo = self.repo_by_name(str(data["repo"]))
            number, branch = int(data["number"]), str(data["branch"])
            pull = repo.pulls.get(number)
            if pull is None:
                pull = FakePull(
                    number,
                    branch,
                    repo.branches[branch],
                    repo.default_branch,
                    str(data.get("title") or f"Change #{number}"),
                    "developer",
                )
                repo.pulls[number] = pull
            pull.head_sha = repo.branches[branch]
            return 201, {}, {"number": number, "head_sha": pull.head_sha}
        return 404, {}, {"message": f"{LABEL} unknown control endpoint"}

    def _control_deliver(self, data: dict[str, Any]) -> tuple[int, dict[str, str], Any]:
        """Build a payload for ``event`` and post it signed (outside the lock: refactorX may call
        back into this fake while handling it)."""
        if not self.webhook_url or not self.webhook_secret:
            return 409, {}, {"message": f"{LABEL} webhook target not configured"}
        with self.lock:
            repo = self.repo_by_name(str(data["repo"]))
            installation = next(i for i in self.installations.values() if repo.id in i.repo_ids)
            event = str(data["event"])
            if event == "push":
                payload = self.push_payload(
                    repo, installation.id, data.get("before"), repo.branches[repo.default_branch]
                )
            else:
                payload = self.pull_payload(
                    repo, installation.id, str(data.get("action") or "opened"), int(data["number"])
                )
        response = self.deliver(self.webhook_url, self.webhook_secret, event, payload)
        return response.status_code, {}, response.json()

    # -- webhooks -----------------------------------------------------------------------------

    @staticmethod
    def sign(secret: str, body: bytes) -> str:
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def deliver(
        self,
        url: str,
        secret: str,
        event: str,
        payload: dict[str, Any],
        *,
        delivery: str | None = None,
        signature: str | None = None,
    ) -> httpx.Response:
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery or secrets.token_hex(16),
            "X-Hub-Signature-256": signature if signature is not None else self.sign(secret, body),
            "User-Agent": "GitHub-Hookshot/fake",
        }
        return httpx.post(url, content=body, headers=headers, timeout=30)

    def push_payload(
        self,
        repo: FakeRepo,
        installation: int,
        before: str | None,
        after: str,
        *,
        branch: str | None = None,
    ) -> dict[str, Any]:
        branch = branch or repo.default_branch
        return {
            "ref": f"refs/heads/{branch}",
            "before": before or "0" * 40,
            "after": after,
            "deleted": after == "0" * 40,
            "forced": False,
            "repository": repo.as_json(),
            "installation": {"id": installation},
            "sender": {"login": "developer"},
        }

    def pull_payload(
        self, repo: FakeRepo, installation: int, action: str, number: int
    ) -> dict[str, Any]:
        return {
            "action": action,
            "number": number,
            "pull_request": self._pull_json(repo, repo.pulls[number]),
            "repository": repo.as_json(),
            "installation": {"id": installation},
            "sender": {"login": "developer"},
        }

    def installation_payload(self, action: str, installation: FakeInstallation) -> dict[str, Any]:
        return {
            "action": action,
            "installation": installation.as_json(self.app_id),
            "repositories": [
                {"id": r, "full_name": self.repos[r].full_name}
                for r in installation.repo_ids
                if r in self.repos
            ],
            "sender": {"login": installation.account},
        }


class _Reply(Exception):  # control flow inside the fake
    def __init__(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        super().__init__(status)
        self.status = status
        self.payload = payload
        self.headers = headers or {}
