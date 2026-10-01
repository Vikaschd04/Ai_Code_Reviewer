"""GitHub App client (P06; ADR 0015).

It covers:
- app authentication and repository-scoped installation tokens;
- the REST subset refactorX uses;
- webhook signature checks;
- user authorization for linking installations.

Reading and publishing use different tokens. Every operation asks GitHub for an installation
token limited to one repository and to the permissions that operation needs (``READ``,
``PUBLISH_CHECKS``, ``PUBLISH_FIXES``). Tokens live in memory until shortly before they expire and
are never stored or logged. Nothing here runs Git: code arrives as GitHub's archive of an exact
commit, so no repository hooks, filters or configuration can execute.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import IO, Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from crp_core.config import Settings, is_loopback_host
from crp_core.local_secrets import SecretFileError, read_secret_file

logger = logging.getLogger(__name__)

API_VERSION = "2026-03-10"
USER_AGENT = "refactorX"
READ = {"contents": "read", "metadata": "read", "pull_requests": "read"}
PUBLISH_CHECKS = {"checks": "write", "pull_requests": "write", "metadata": "read"}
PUBLISH_FIXES = {"contents": "write", "pull_requests": "write", "metadata": "read"}
_TOKEN_MARGIN_SECONDS = 300
_MAX_PAGES = 50
_RETRY_STATUSES = frozenset({500, 502, 503, 504})
_CHUNK = 256 * 1024


# -- setup -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GitHubSetup:
    """What this server can do with GitHub; secrets are kept out of ``repr``."""

    app_ready: bool  # can authenticate as the app (reviews, publication)
    linking_ready: bool  # can verify installations through user authorization
    webhooks_ready: bool  # can verify webhook signatures
    reason: str | None  # plain explanation when the app is not ready
    admin_hint: str | None
    app_id: int | None
    client_id: str | None
    slug: str | None
    api_url: str
    web_url: str
    callback_url: str | None
    timeout: float
    _private_key: bytes | None = field(default=None, repr=False)
    _webhook_secret: bytes | None = field(default=None, repr=False)
    _client_secret: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        return self.app_ready

    @property
    def install_url(self) -> str | None:
        return f"{self.web_url}/apps/{self.slug}/installations/new" if self.slug else None

    @property
    def issuer(self) -> str | None:
        """JWT issuer: GitHub recommends the client ID; the app ID is accepted too."""
        if self.client_id:
            return self.client_id
        return str(self.app_id) if self.app_id is not None else None


def _secret(
    value: Any, path: Any, env: str, *, keep_newlines: bool = False
) -> tuple[str | None, str | None]:
    if value is not None and value.get_secret_value().strip():
        text = value.get_secret_value().strip()
        return (text.replace("\\n", "\n") if keep_newlines else text), None
    if path is not None:
        try:
            return read_secret_file(path, min_length=1), None
        except SecretFileError as exc:
            return None, f"{env}_FILE cannot be used: {exc}"
    return None, None


def resolve_github(settings: Settings) -> GitHubSetup:
    key, key_problem = _secret(
        settings.github_private_key,
        settings.github_private_key_file,
        "CRP_GITHUB_PRIVATE_KEY",
        keep_newlines=True,
    )
    hook, hook_problem = _secret(
        settings.github_webhook_secret,
        settings.github_webhook_secret_file,
        "CRP_GITHUB_WEBHOOK_SECRET",
    )
    client_secret, secret_problem = _secret(
        settings.github_client_secret,
        settings.github_client_secret_file,
        "CRP_GITHUB_CLIENT_SECRET",
    )
    reason = hint = None
    private_key: bytes | None = None
    if settings.github_app_id is None and not settings.github_client_id:
        reason = "GitHub is not set up on this server."
        hint = "Register a GitHub App and set CRP_GITHUB_APP_ID, CRP_GITHUB_CLIENT_ID and a key."
    elif key is None:
        reason = "GitHub is not fully set up on this server."
        hint = key_problem or "Set CRP_GITHUB_PRIVATE_KEY or CRP_GITHUB_PRIVATE_KEY_FILE."
    else:
        try:
            loaded = serialization.load_pem_private_key(key.encode(), password=None)
        except ValueError, TypeError:
            loaded = None
        if not isinstance(loaded, rsa.RSAPrivateKey):
            reason = "GitHub is not fully set up on this server."
            hint = "The GitHub App private key is not a readable RSA PEM key."
        else:
            private_key = key.encode()
    linking = (
        private_key is not None
        and bool(settings.github_client_id)
        and client_secret is not None
        and settings.github_app_slug is not None
    )
    if private_key is not None and not linking and hint is None:
        hint = secret_problem or (
            "Set CRP_GITHUB_CLIENT_ID, CRP_GITHUB_CLIENT_SECRET and CRP_GITHUB_APP_SLUG so admins "
            "can link installations."
        )
    if private_key is not None and hook is None and hint is None:
        hint = (
            hook_problem or "Set CRP_GITHUB_WEBHOOK_SECRET to receive push and pull request events."
        )
    return GitHubSetup(
        app_ready=private_key is not None,
        linking_ready=linking,
        webhooks_ready=hook is not None,
        reason=reason,
        admin_hint=hint,
        app_id=settings.github_app_id,
        client_id=settings.github_client_id,
        slug=settings.github_app_slug,
        api_url=settings.github_api_url,
        web_url=settings.github_web_url,
        callback_url=settings.github_callback_url,
        timeout=settings.github_request_timeout_seconds,
        _private_key=private_key,
        _webhook_secret=hook.encode() if hook else None,
        _client_secret=client_secret,
    )


# -- signatures and app tokens -------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def app_jwt(issuer: str, private_key_pem: bytes, *, now: int | None = None) -> str:
    """RS256 JWT for the app: issued 60 s in the past (clock drift), valid for 9 minutes."""
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("the GitHub App key must be an RSA key")
    issued = int(time.time() if now is None else now) - 60
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"iat": issued, "exp": issued + 600 - 60, "iss": issuer}
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def verify_signature(secret: bytes, body: bytes, header: str | None) -> bool:
    """Check ``X-Hub-Signature-256`` (``sha256=<hex HMAC>``) in constant time."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def webhook_secret(setup: GitHubSetup) -> bytes | None:
    return setup._webhook_secret


def git_blob_id(data: bytes) -> str:
    """Git's object id of a file's bytes (SHA-1 of ``blob <size>\\0`` + bytes)."""
    return hashlib.sha1(b"blob %d\x00" % len(data) + data, usedforsecurity=False).hexdigest()


# -- errors ----------------------------------------------------------------------------------------


class GitHubError(Exception):
    """A GitHub request failed. ``code`` is stable; ``message`` is safe to show and log."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class GitHubUnavailableError(GitHubError):
    """GitHub could not be reached, failed (5xx) or rate-limited us; retry later."""


class GitHubAccessError(GitHubError):
    """The installation, repository or permission is no longer available to the app."""


class GitHubNotFoundError(GitHubError):
    """The branch, commit or pull request does not exist (or is not visible)."""


class GitHubConflictError(GitHubError):
    """GitHub refused the change (for example, a branch that already exists)."""


@dataclass(frozen=True, slots=True)
class RepoAccess:
    """Which repository an operation may touch, through which installation."""

    installation_id: int
    repository_id: int
    full_name: str

    @property
    def path(self) -> str:
        owner, _, name = self.full_name.partition("/")
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"


@dataclass(frozen=True, slots=True)
class TreeEntry:
    path: str
    mode: str  # 100644, 100755, 120000 (symlink), 160000 (submodule), 040000 (tree)
    kind: str  # blob, tree, commit
    sha: str
    size: int | None


@dataclass(frozen=True, slots=True)
class PullRequestInfo:
    number: int
    state: str
    title: str
    author: str | None
    url: str
    head_sha: str
    head_ref: str
    head_repository_id: int | None
    base_sha: str
    base_ref: str
    base_repository_id: int
    draft: bool

    @property
    def fork(self) -> bool:
        return self.head_repository_id != self.base_repository_id


@dataclass(slots=True)
class _Token:
    value: str
    expires: float


def _parse_time(value: str | None) -> float:
    if not value:
        return time.time() + 3000
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _next_link(header: str | None) -> str | None:
    for part in (header or "").split(","):
        section = part.split(";")
        if len(section) >= 2 and section[1].strip() == 'rel="next"':
            return section[0].strip().strip("<>")
    return None


class GitHubClient:
    """Async GitHub REST client for one server; share one instance per process."""

    def __init__(
        self,
        setup: GitHubSetup,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._setup = setup
        self._http = httpx.AsyncClient(
            timeout=setup.timeout,
            transport=transport,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
        )
        self._tokens: dict[tuple[int, tuple[int, ...], tuple[str, ...]], _Token] = {}
        self._sleep = sleep

    @property
    def setup(self) -> GitHubSetup:
        return self._setup

    async def close(self) -> None:
        await self._http.aclose()

    # -- plumbing -------------------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self._setup.api_url}{path}"

    def _same_api_host(self, url: str) -> bool:
        return urlsplit(url).netloc == urlsplit(self._setup.api_url).netloc

    @staticmethod
    def _headers(token: str | None) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _app_token(self) -> str:
        issuer = self._setup.issuer
        if not self._setup.app_ready or issuer is None or self._setup._private_key is None:
            raise GitHubAccessError("app_not_configured", "GitHub is not set up on this server.")
        return app_jwt(issuer, self._setup._private_key)

    async def _send(
        self,
        method: str,
        url: str,
        token: str | None,
        *,
        body: Any = None,
        params: dict[str, Any] | None = None,
        retry: bool,
    ) -> httpx.Response:
        attempts = 3 if retry else 1
        for attempt in range(1, attempts + 1):
            try:
                response = await self._http.request(
                    method, self._url(url), headers=self._headers(token), json=body, params=params
                )
            except httpx.HTTPError as exc:
                if attempt == attempts:
                    raise GitHubUnavailableError(
                        "github_unreachable", f"GitHub could not be reached ({type(exc).__name__})"
                    ) from exc
                await self._sleep(0.5 * attempt)
                continue
            limited = response.status_code == 429 or (
                response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
            )
            if (response.status_code in _RETRY_STATUSES or limited) and attempt < attempts:
                wait = float(response.headers.get("retry-after") or 0.5 * attempt)
                await self._sleep(min(wait, 10.0))
                continue
            return response
        raise AssertionError("unreachable")

    @staticmethod
    def _raise_for(response: httpx.Response, what: str) -> None:
        status = response.status_code
        if status < 400:
            return
        try:
            message = str(response.json().get("message", ""))[:200]
        except ValueError, AttributeError:
            message = ""
        detail = f"{what}: GitHub answered {status}" + (f" ({message})" if message else "")
        if status == 429 or (
            status == 403 and response.headers.get("x-ratelimit-remaining") == "0"
        ):
            raise GitHubUnavailableError("github_rate_limited", detail, status)
        if status >= 500:
            raise GitHubUnavailableError("github_unavailable", detail, status)
        if status in {401, 403, 451}:
            raise GitHubAccessError("github_access_denied", detail, status)
        if status == 404:
            raise GitHubNotFoundError("github_not_found", detail, status)
        if status in {409, 422}:
            raise GitHubConflictError("github_conflict", detail, status)
        raise GitHubError("github_error", detail, status)

    async def _json(
        self,
        method: str,
        path: str,
        token: str | None,
        what: str,
        *,
        body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._send(
            method, path, token, body=body, params=params, retry=method == "GET"
        )
        self._raise_for(response, what)
        return response.json() if response.content else None

    async def _pages(
        self, path: str, token: str, what: str, key: str | None, *, limit: int = 5000
    ) -> list[Any]:
        items: list[Any] = []
        url: str | None = path
        params: dict[str, Any] | None = {"per_page": 100}
        for _ in range(_MAX_PAGES):
            if url is None or len(items) >= limit:
                break
            response = await self._send("GET", url, token, params=params, retry=True)
            self._raise_for(response, what)
            data = response.json()
            items.extend(data[key] if key else data)
            following = _next_link(response.headers.get("link"))
            url = following if following and self._same_api_host(following) else None
            params = None  # the next link carries its own query
        return items[:limit]

    # -- installation tokens ----------------------------------------------------------------------

    async def installation_token(
        self, installation_id: int, repository_ids: list[int], permissions: dict[str, str]
    ) -> str:
        key = (installation_id, tuple(sorted(repository_ids)), tuple(sorted(permissions.items())))
        flat_key = (key[0], key[1], tuple(f"{k}={v}" for k, v in key[2]))
        cached = self._tokens.get(flat_key)
        if cached is not None and cached.expires - time.time() > _TOKEN_MARGIN_SECONDS:
            return cached.value
        body: dict[str, Any] = {"permissions": permissions}
        if repository_ids:
            body["repository_ids"] = repository_ids
        response = await self._send(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            self._app_token(),
            body=body,
            retry=True,
        )
        if response.status_code == 404:
            raise GitHubAccessError(
                "installation_not_found", "The GitHub App is no longer installed there.", 404
            )
        if response.status_code == 403:
            raise GitHubAccessError(
                "installation_suspended", "The GitHub App installation is suspended.", 403
            )
        if response.status_code == 422:
            raise GitHubAccessError(
                "permission_not_granted",
                "The GitHub App was not granted the permission this needs: "
                + ", ".join(f"{k} ({v})" for k, v in sorted(permissions.items())),
                422,
            )
        self._raise_for(response, "creating an installation token")
        data = response.json()
        token = _Token(str(data["token"]), _parse_time(data.get("expires_at")))
        self._tokens[flat_key] = token
        return token.value

    def forget_tokens(self, installation_id: int) -> None:
        for key in [k for k in self._tokens if k[0] == installation_id]:
            del self._tokens[key]

    async def _repo_token(self, access: RepoAccess, permissions: dict[str, str]) -> str:
        return await self.installation_token(
            access.installation_id, [access.repository_id], permissions
        )

    async def _repo_json(
        self,
        access: RepoAccess,
        permissions: dict[str, str],
        method: str,
        path: str,
        what: str,
        *,
        body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """A repository request with a scoped token; a revoked cached token is replaced once."""
        token = await self._repo_token(access, permissions)
        try:
            return await self._json(method, path, token, what, body=body, params=params)
        except GitHubAccessError as exc:
            if exc.status != 401:
                raise
        self.forget_tokens(access.installation_id)
        token = await self._repo_token(access, permissions)  # a gone installation raises here
        return await self._json(method, path, token, what, body=body, params=params)

    # -- app-level reads ------------------------------------------------------------------------

    async def installation(self, installation_id: int) -> dict[str, Any]:
        return dict(
            await self._json(
                "GET", f"/app/installations/{installation_id}", self._app_token(), "installation"
            )
        )

    async def installation_repositories(self, installation_id: int) -> list[dict[str, Any]]:
        token = await self.installation_token(installation_id, [], {"metadata": "read"})
        return await self._pages(
            "/installation/repositories", token, "listing repositories", "repositories"
        )

    # -- repository reads (READ token) ------------------------------------------------------------

    async def repository(self, access: RepoAccess) -> dict[str, Any]:
        return dict(
            await self._repo_json(
                access, READ, "GET", f"/repositories/{access.repository_id}", "repository"
            )
        )

    async def branch_head(self, access: RepoAccess, branch: str) -> str:
        data = await self._repo_json(
            access,
            READ,
            "GET",
            f"{access.path}/git/ref/heads/{quote(branch, safe='/')}",
            f"branch {branch}",
        )
        return str(data["object"]["sha"])

    async def pull_request(self, access: RepoAccess, number: int) -> PullRequestInfo:
        data = await self._repo_json(
            access, READ, "GET", f"{access.path}/pulls/{number}", f"pull request #{number}"
        )
        head, base = data["head"], data["base"]
        return PullRequestInfo(
            number=int(data["number"]),
            state="merged" if data.get("merged") else str(data["state"]),
            title=str(data.get("title") or "")[:300],
            author=(data.get("user") or {}).get("login"),
            url=str(data.get("html_url") or ""),
            head_sha=str(head["sha"]),
            head_ref=str(head["ref"]),
            head_repository_id=(head.get("repo") or {}).get("id"),
            base_sha=str(base["sha"]),
            base_ref=str(base["ref"]),
            base_repository_id=int(base["repo"]["id"]),
            draft=bool(data.get("draft")),
        )

    async def merge_base(self, access: RepoAccess, base: str, head: str) -> str:
        data = await self._repo_json(
            access,
            READ,
            "GET",
            f"{access.path}/compare/{quote(base, safe='')}...{quote(head, safe='')}",
            "comparing commits",
            params={"per_page": 1},
        )
        return str(data["merge_base_commit"]["sha"])

    async def commit_tree(self, access: RepoAccess, sha: str) -> str:
        data = await self._repo_json(
            access, READ, "GET", f"{access.path}/git/commits/{sha}", "commit"
        )
        return str(data["tree"]["sha"])

    async def tree(self, access: RepoAccess, tree_sha: str) -> tuple[list[TreeEntry], bool]:
        """Every entry of the commit's tree, and whether GitHub truncated the listing."""
        data = await self._repo_json(
            access,
            READ,
            "GET",
            f"{access.path}/git/trees/{tree_sha}",
            "listing the commit's files",
            params={"recursive": "1"},
        )
        entries = [
            TreeEntry(
                str(item["path"]),
                str(item["mode"]),
                str(item["type"]),
                str(item["sha"]),
                item.get("size"),
            )
            for item in data.get("tree", [])
        ]
        return entries, bool(data.get("truncated"))

    async def blob(self, access: RepoAccess, sha: str, *, max_bytes: int) -> bytes:
        data = await self._repo_json(
            access, READ, "GET", f"{access.path}/git/blobs/{sha}", "file content"
        )
        if int(data.get("size", 0)) > max_bytes:
            raise GitHubError("blob_too_large", "A file is larger than the per-file limit.")
        if data.get("encoding") != "base64":
            raise GitHubError("blob_encoding", "GitHub returned a file in an unexpected encoding.")
        content = base64.b64decode(str(data.get("content", "")))
        if git_blob_id(content) != sha:
            raise GitHubError("blob_mismatch", "A file GitHub returned does not match its id.")
        return content

    async def download_archive(
        self, access: RepoAccess, sha: str, sink: IO[bytes], *, max_bytes: int
    ) -> int:
        """Stream GitHub's ZIP archive of an exact commit into ``sink``; returns the byte count.

        The API answers with a short-lived redirect; the archive host gets no credentials.
        """
        token = await self._repo_token(access, READ)
        response = await self._send("GET", f"{access.path}/zipball/{sha}", token, retry=True)
        if response.status_code == 401:  # a revoked cached token: mint a fresh one once
            self.forget_tokens(access.installation_id)
            token = await self._repo_token(access, READ)
            response = await self._send("GET", f"{access.path}/zipball/{sha}", token, retry=True)
        location = response.headers.get("location")
        if response.status_code in {301, 302, 303, 307, 308} and location:
            target = urlsplit(location)
            if target.scheme != "https" and not (
                target.scheme == "http" and is_loopback_host(target.hostname or "")
            ):
                raise GitHubError("archive_redirect", "GitHub redirected to an insecure address.")
            url, headers = location, {"User-Agent": USER_AGENT}
        else:
            self._raise_for(response, "downloading the commit")
            url, headers = self._url(f"{access.path}/zipball/{sha}"), self._headers(token)
        written = 0
        try:
            async with self._http.stream("GET", url, headers=headers) as stream:
                if stream.status_code >= 400:
                    await stream.aread()
                    self._raise_for(stream, "downloading the commit")
                async for chunk in stream.aiter_bytes(_CHUNK):
                    written += len(chunk)
                    if written > max_bytes:
                        raise GitHubError(
                            "archive_too_large",
                            "The repository archive exceeds the upload size limit.",
                        )
                    sink.write(chunk)
        except httpx.HTTPError as exc:
            raise GitHubUnavailableError(
                "github_unreachable", f"Downloading the commit failed ({type(exc).__name__})"
            ) from exc
        return written

    # -- publication (write tokens; callers check the project's policy first) -------------------

    async def create_check_run(self, access: RepoAccess, payload: dict[str, Any]) -> int:
        data = await self._repo_json(
            access,
            PUBLISH_CHECKS,
            "POST",
            f"{access.path}/check-runs",
            "creating the check",
            body=payload,
        )
        return int(data["id"])

    async def update_check_run(
        self, access: RepoAccess, check_run_id: int, payload: dict[str, Any]
    ) -> None:
        await self._repo_json(
            access,
            PUBLISH_CHECKS,
            "PATCH",
            f"{access.path}/check-runs/{check_run_id}",
            "updating the check",
            body=payload,
        )

    async def upsert_comment(
        self, access: RepoAccess, number: int, body: str, comment_id: int | None
    ) -> int:
        """Update refactorX's earlier summary comment, or post one if it is gone."""
        if comment_id is not None:
            try:
                await self._repo_json(
                    access,
                    PUBLISH_CHECKS,
                    "PATCH",
                    f"{access.path}/issues/comments/{comment_id}",
                    "updating the comment",
                    body={"body": body},
                )
            except GitHubNotFoundError:
                pass  # deleted on GitHub: post a new one
            else:
                return comment_id
        data = await self._repo_json(
            access,
            PUBLISH_CHECKS,
            "POST",
            f"{access.path}/issues/{number}/comments",
            "posting the comment",
            body={"body": body},
        )
        return int(data["id"])

    async def create_fix_commit(
        self,
        access: RepoAccess,
        *,
        parent_sha: str,
        base_tree: str,
        path: str,
        mode: str,
        content: bytes,
        message: str,
    ) -> str:
        blob = await self._repo_json(
            access,
            PUBLISH_FIXES,
            "POST",
            f"{access.path}/git/blobs",
            "uploading the fixed file",
            body={"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"},
        )
        tree = await self._repo_json(
            access,
            PUBLISH_FIXES,
            "POST",
            f"{access.path}/git/trees",
            "creating the fixed tree",
            body={
                "base_tree": base_tree,
                "tree": [{"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]}],
            },
        )
        commit = await self._repo_json(
            access,
            PUBLISH_FIXES,
            "POST",
            f"{access.path}/git/commits",
            "creating the fix commit",
            body={"message": message, "tree": tree["sha"], "parents": [parent_sha]},
        )
        return str(commit["sha"])

    async def create_branch(self, access: RepoAccess, branch: str, sha: str) -> None:
        await self._repo_json(
            access,
            PUBLISH_FIXES,
            "POST",
            f"{access.path}/git/refs",
            "creating the fix branch",
            body={"ref": f"refs/heads/{branch}", "sha": sha},
        )

    async def create_pull_request(
        self, access: RepoAccess, *, head: str, base: str, title: str, body: str
    ) -> tuple[int, str]:
        data = await self._repo_json(
            access,
            PUBLISH_FIXES,
            "POST",
            f"{access.path}/pulls",
            "opening the pull request",
            body={"head": head, "base": base, "title": title, "body": body},
        )
        return int(data["number"]), str(data["html_url"])

    # -- user authorization (linking installations) ---------------------------------------------

    def authorize_url(self, state: str) -> str:
        if self._setup.client_id is None:
            raise GitHubAccessError("linking_not_configured", "GitHub linking is not set up.")
        query = {"client_id": self._setup.client_id, "state": state}
        if self._setup.callback_url:
            query["redirect_uri"] = self._setup.callback_url
        return f"{self._setup.web_url}/login/oauth/authorize?{urlencode(query)}"

    async def exchange_code(self, code: str) -> str:
        """Exchange the authorization code for a short-lived user token (used once, not kept)."""
        if not self._setup.linking_ready or self._setup._client_secret is None:
            raise GitHubAccessError("linking_not_configured", "GitHub linking is not set up.")
        form = {
            "client_id": self._setup.client_id or "",
            "client_secret": self._setup._client_secret,
            "code": code,
        }
        if self._setup.callback_url:
            form["redirect_uri"] = self._setup.callback_url
        try:
            response = await self._http.post(
                f"{self._setup.web_url}/login/oauth/access_token",
                data=form,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailableError(
                "github_unreachable", "GitHub could not be reached"
            ) from exc
        self._raise_for(response, "confirming the GitHub sign-in")
        data = response.json()
        if "access_token" not in data:
            raise GitHubAccessError(
                "authorization_failed",
                str(data.get("error_description") or "GitHub did not confirm the sign-in")[:200],
            )
        return str(data["access_token"])

    async def user_installations(self, user_token: str) -> list[dict[str, Any]]:
        """Installations of this app that the signed-in GitHub user can access."""
        return await self._pages(
            "/user/installations", user_token, "listing your installations", "installations"
        )
