"""GitHub App status, verified installation linking, repositories and webhooks (P06; ADR 0015).

Linking proves the admin controls an installation. GitHub's install redirect carries an
``installation_id`` anyone could forge, so it is never trusted. Instead:
1. The admin authorizes refactorX on GitHub (one-time state bound to them and the workspace).
2. GitHub redirects back; the callback only forwards code and state to the web app, because the
   session cookie is ``SameSite=Strict``.
3. The web app completes the link with a normal authenticated request.
4. The server lists the installations that GitHub user can access and links those. The user
   token is used once and discarded.

Webhooks are accepted only with a valid ``X-Hub-Signature-256``. Each delivery id is recorded, so
redelivery is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.sources.github import (
    GitHubAccessError,
    GitHubClient,
    GitHubError,
    resolve_github,
    verify_signature,
    webhook_secret,
)
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.auth.principal import Principal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    GitHubLinkComplete,
    GitHubLinkResult,
    GitHubLinkStart,
    GitHubStatus,
    GitInstallationList,
    GitInstallationResponse,
    GitLinkSkip,
)
from crp_api.services import git as git_service
from crp_core.db.models import (
    CodeReview,
    GitConnection,
    GitDelivery,
    GitInstallation,
    GitLinkRequest,
    GitRepository,
)
from crp_core.db.session import transaction
from crp_core.domain.states import (
    CodeReviewKind,
    CodeReviewTrigger,
    GitProvider,
    MembershipRole,
)

router = APIRouter(tags=["github"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
_STATE_TTL = timedelta(minutes=10)
_DELIVERY = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_EVENT = re.compile(r"^[a-z_]{1,64}$")
_PR_ACTIONS = frozenset({"opened", "reopened", "synchronize", "ready_for_review"})


def _client(container: Any) -> GitHubClient:
    client: GitHubClient | None = container.github
    if client is None:
        raise ApiError(503, "github_not_configured", "GitHub is not set up on this server")
    return client


def _require_admin(principal: Principal, workspace_id: uuid.UUID) -> None:
    if principal.role_in(workspace_id) is None:
        raise ApiError(404, "workspace_not_found", "Workspace not found")
    if principal.is_demo:
        raise ApiError(403, "demo_not_allowed", "The demo account cannot connect GitHub")
    if not principal.has_role(workspace_id, MembershipRole.ADMIN):
        raise ApiError(403, "forbidden", "Only workspace admins can manage GitHub connections")


def _web(container: Any, fragment: str) -> str:
    origins = container.settings.allowed_web_origins
    base = origins[0].rstrip("/") if origins else ""
    return f"{base}/#{fragment}"


@router.get("/github/status", response_model=GitHubStatus)
async def github_status(principal: CurrentPrincipal, container: Container) -> GitHubStatus:
    setup = resolve_github(container.settings)
    admin = any(principal.has_role(w, MembershipRole.ADMIN) for w in principal.workspace_ids)
    return GitHubStatus(
        available=setup.app_ready,
        linking_available=setup.linking_ready,
        webhooks_available=setup.webhooks_ready,
        reason=setup.reason,
        admin_hint=setup.admin_hint if admin and not principal.is_demo else None,
        install_url=setup.install_url if setup.app_ready else None,
    )


# -- linking ---------------------------------------------------------------------------------------


@router.post(
    "/workspaces/{workspace_id}/github/link", response_model=GitHubLinkStart, responses=_ERRORS
)
async def start_link(
    workspace_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> GitHubLinkStart:
    """Begin verified linking: returns the GitHub page where the admin confirms access."""
    _require_admin(principal, workspace_id)
    client = _client(container)
    if not client.setup.linking_ready:
        raise ApiError(503, "linking_not_configured", "GitHub linking is not fully set up")
    state = secrets.token_urlsafe(32)
    async with transaction(container.session_factory) as session:
        session.add(
            GitLinkRequest(
                workspace_id=workspace_id,
                user_id=principal.user_id,
                state_sha256=hashlib.sha256(state.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + _STATE_TTL,
            )
        )
    return GitHubLinkStart(authorize_url=client.authorize_url(state))


@router.get("/github/callback", include_in_schema=False)
async def link_callback(
    container: Container,
    code: Annotated[str | None, Query(max_length=200)] = None,
    state: Annotated[str | None, Query(max_length=200)] = None,
    error: Annotated[str | None, Query(max_length=100)] = None,
) -> RedirectResponse:
    """GitHub's redirect after authorization: hand code and state to the web app (no DB access)."""
    if error or not code or not state:
        query = urlencode({"error": (error or "missing_code")[:100]})
    else:
        query = urlencode({"code": code, "state": state})
    return RedirectResponse(_web(container, f"/github?{query}"), status_code=303)


@router.get("/github/setup", include_in_schema=False)
async def install_setup(container: Container) -> RedirectResponse:
    """GitHub's redirect after installing the app; its installation_id is never trusted."""
    return RedirectResponse(_web(container, "/github?installed=1"), status_code=303)


async def _link_installation(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    item: dict[str, Any],
) -> tuple[GitInstallation | None, str | None]:
    external = int(item["id"])
    account = (item.get("account") or {}).get("login") or str(external)
    existing = (
        await session.execute(
            select(GitInstallation).where(
                GitInstallation.provider == GitProvider.GITHUB.value,
                GitInstallation.external_id == external,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.workspace_id != workspace_id:
        if existing.revoked_at is None:
            return None, f"{account} is already linked to another workspace"
        await session.delete(existing)  # uninstalled and reinstalled: start over here
        await session.flush()
        existing = None
    if existing is None:
        existing = GitInstallation(
            workspace_id=workspace_id,
            provider=GitProvider.GITHUB.value,
            external_id=external,
            account_login=str(account)[:255],
            account_type=str((item.get("account") or {}).get("type") or "User")[:32],
            permissions={},
            linked_by=user_id,
        )
        session.add(existing)
    existing.repository_selection = item.get("repository_selection")
    existing.permissions = dict(item.get("permissions") or {})
    existing.suspended_at = datetime.now(UTC) if item.get("suspended_at") else None
    existing.revoked_at = None
    await session.flush()
    return existing, None


@router.post(
    "/workspaces/{workspace_id}/github/link/complete",
    response_model=GitHubLinkResult,
    responses=_ERRORS,
)
async def complete_link(
    workspace_id: uuid.UUID,
    body: GitHubLinkComplete,
    principal: CurrentPrincipal,
    container: Container,
) -> GitHubLinkResult:
    """Finish linking with GitHub's one-time code: link the installations this user can access."""
    _require_admin(principal, workspace_id)
    client = _client(container)
    digest = hashlib.sha256(body.state.encode()).hexdigest()
    async with transaction(container.session_factory) as session:
        request = (
            await session.execute(
                select(GitLinkRequest)
                .where(GitLinkRequest.state_sha256 == digest)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            request is None
            or request.user_id != principal.user_id
            or request.workspace_id != workspace_id
        ):
            raise ApiError(403, "link_state_invalid", "This GitHub confirmation is not yours")
        if request.used_at is not None:
            raise ApiError(409, "link_state_used", "This GitHub confirmation was already used")
        if request.expires_at < datetime.now(UTC):
            raise ApiError(
                409, "link_state_expired", "The GitHub confirmation expired; start again"
            )
        request.used_at = datetime.now(UTC)
    try:
        user_token = await client.exchange_code(body.code)
        found = await client.user_installations(user_token)
    except GitHubAccessError as exc:
        raise ApiError(403, "github_authorization_failed", exc.message) from exc
    except GitHubError as exc:
        raise ApiError(503, "github_unavailable", exc.message) from exc
    if client.setup.app_id is not None:
        found = [
            i for i in found if int(i.get("app_id") or client.setup.app_id) == client.setup.app_id
        ]
    linked: list[GitInstallation] = []
    skipped: list[GitLinkSkip] = []
    async with transaction(container.session_factory) as session:
        for item in found:
            installation, reason = await _link_installation(
                session, workspace_id, principal.user_id, item
            )
            account = str((item.get("account") or {}).get("login") or item.get("id"))
            if installation is None:
                skipped.append(GitLinkSkip(account=account, reason=reason or "not linked"))
                continue
            try:
                await git_service.sync_installation(session, client, installation)
            except GitHubError as exc:
                skipped.append(GitLinkSkip(account=account, reason=exc.message))
                continue
            linked.append(installation)
        stored = await git_service.repositories(session, [i.id for i in linked])
        result = [git_service.installation_response(i, stored[i.id]) for i in linked]
        request_row = (
            await session.execute(
                select(GitLinkRequest).where(GitLinkRequest.state_sha256 == digest)
            )
        ).scalar_one()
        request_row.result = {"linked": len(result), "skipped": len(skipped)}
    return GitHubLinkResult(linked=result, skipped=skipped)


@router.get(
    "/workspaces/{workspace_id}/github/installations",
    response_model=GitInstallationList,
    responses=_ERRORS,
)
async def list_installations(
    workspace_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> GitInstallationList:
    if not principal.has_role(workspace_id, MembershipRole.MEMBER):
        raise ApiError(404, "workspace_not_found", "Workspace not found")
    async with transaction(container.session_factory) as session:
        installations = list(
            (
                await session.execute(
                    select(GitInstallation)
                    .where(GitInstallation.workspace_id == workspace_id)
                    .order_by(GitInstallation.account_login)
                )
            ).scalars()
        )
        repos = await git_service.repositories(session, [i.id for i in installations])
        return GitInstallationList(
            items=[git_service.installation_response(i, repos[i.id]) for i in installations]
        )


async def _installation(
    session: AsyncSession, workspace_id: uuid.UUID, installation_id: uuid.UUID
) -> GitInstallation:
    installation = await session.get(GitInstallation, installation_id, with_for_update=True)
    if installation is None or installation.workspace_id != workspace_id:
        raise ApiError(404, "installation_not_found", "GitHub installation not found")
    return installation


@router.post(
    "/workspaces/{workspace_id}/github/installations/{installation_id}/sync",
    response_model=GitInstallationResponse,
    responses=_ERRORS,
)
async def sync_installation(
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
) -> GitInstallationResponse:
    """Refresh the repository list from GitHub (admins)."""
    _require_admin(principal, workspace_id)
    client = _client(container)
    async with transaction(container.session_factory) as session:
        installation = await _installation(session, workspace_id, installation_id)
        try:
            await git_service.sync_installation(session, client, installation)
        except GitHubAccessError as exc:
            if exc.code == "installation_not_found":
                installation.revoked_at = datetime.now(UTC)
            raise ApiError(409, exc.code, exc.message) from exc
        except GitHubError as exc:
            raise ApiError(503, "github_unavailable", exc.message) from exc
        repos = await git_service.repositories(session, [installation.id])
        return git_service.installation_response(installation, repos[installation.id])


@router.delete(
    "/workspaces/{workspace_id}/github/installations/{installation_id}",
    status_code=204,
    responses=_ERRORS,
)
async def unlink_installation(
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
) -> None:
    """Unlink an installation: its repositories disconnect; reviews stay as history."""
    _require_admin(principal, workspace_id)
    async with transaction(container.session_factory) as session:
        installation = await _installation(session, workspace_id, installation_id)
        connections = (
            select(GitConnection.id)
            .join(GitRepository, GitRepository.id == GitConnection.repository_id)
            .where(GitRepository.installation_id == installation.id)
        )
        canceled = await git_service.request_cancel(
            session, CodeReview.connection_id.in_(connections)
        )
        external = installation.external_id
        await session.delete(installation)
    await git_service.cancel_reviews(container.workflows, canceled)
    if container.github is not None:
        container.github.forget_tokens(external)


# -- webhooks --------------------------------------------------------------------------------------


@router.post("/github/webhook", include_in_schema=False)
async def webhook(request: Request, container: Container) -> JSONResponse:
    setup = resolve_github(container.settings)
    secret = webhook_secret(setup)
    if secret is None:
        raise ApiError(503, "webhooks_not_configured", "GitHub webhooks are not set up")
    limit = container.settings.github_webhook_max_bytes
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise ApiError(413, "payload_too_large", "The webhook payload is too large")
    if not verify_signature(secret, body, request.headers.get("x-hub-signature-256")):
        raise ApiError(401, "invalid_signature", "The webhook signature does not match")
    delivery = request.headers.get("x-github-delivery", "")
    event = request.headers.get("x-github-event", "")
    if not _DELIVERY.fullmatch(delivery) or not _EVENT.fullmatch(event):
        raise ApiError(400, "invalid_webhook", "Missing or malformed GitHub delivery headers")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiError(400, "invalid_webhook", "The webhook body is not JSON") from exc
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_webhook", "The webhook body is not a JSON object")
    outcome = await _handle(container, delivery, event, payload)
    return JSONResponse(outcome, status_code=202 if outcome["outcome"] == "accepted" else 200)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except TypeError, ValueError:
        return None


async def _handle(
    container: Any, delivery: str, event: str, payload: dict[str, Any]
) -> dict[str, Any]:
    action = payload.get("action") if isinstance(payload.get("action"), str) else None
    installation_ext = _int((payload.get("installation") or {}).get("id"))
    repository_ext = _int((payload.get("repository") or {}).get("id"))
    to_start: list[uuid.UUID] = []
    to_cancel: list[uuid.UUID] = []
    sync: GitInstallation | None = None
    async with transaction(container.session_factory) as session:
        row_id = (
            await session.execute(
                insert(GitDelivery)
                .values(
                    id=uuid.uuid4(),
                    provider=GitProvider.GITHUB.value,
                    delivery_id=delivery,
                    event=event,
                    action=action[:64] if action else None,
                    installation_external_id=installation_ext,
                    repository_external_id=repository_ext,
                    outcome="received",
                )
                .on_conflict_do_nothing(
                    index_elements=[GitDelivery.provider, GitDelivery.delivery_id]
                )
                .returning(GitDelivery.id)
            )
        ).scalar_one_or_none()
        if row_id is None:
            return {"outcome": "duplicate", "detail": "this delivery was already received"}
        installation = None
        if installation_ext is not None:
            installation = (
                await session.execute(
                    select(GitInstallation).where(
                        GitInstallation.provider == GitProvider.GITHUB.value,
                        GitInstallation.external_id == installation_ext,
                    )
                )
            ).scalar_one_or_none()
        outcome, detail = "ignored", f"{event} events are not used"
        if event == "ping":
            outcome, detail = "ok", "pong"
        elif installation is None:
            detail = "this installation is not linked to a workspace"
        elif event == "installation":
            outcome, detail, sync = _installation_event(installation, action, payload)
            if action == "deleted":
                to_cancel = await _cancel_for_installation(session, installation)
        elif event == "installation_repositories":
            outcome, detail, sync = "accepted", "repository access changed", installation
            removed = {_int(r.get("id")) for r in payload.get("repositories_removed") or []}
            if removed:
                repos = list(
                    (
                        await session.execute(
                            select(GitRepository).where(
                                GitRepository.installation_id == installation.id,
                                GitRepository.external_id.in_([r for r in removed if r]),
                            )
                        )
                    ).scalars()
                )
                for repo in repos:
                    repo.removed_at = datetime.now(UTC)
                to_cancel = await git_service.request_cancel(
                    session,
                    CodeReview.connection_id.in_(
                        select(GitConnection.id).where(
                            GitConnection.repository_id.in_([r.id for r in repos])
                        )
                    ),
                )
        elif event == "repository" and action == "renamed" and repository_ext is not None:
            renamed = await _repository(session, installation, repository_ext)
            if renamed is not None:
                full_name = payload["repository"].get("full_name", renamed.full_name)
                renamed.full_name = str(full_name)[:255]
                outcome, detail = "accepted", "repository renamed"
        elif event in {"push", "pull_request"} and repository_ext is not None:
            outcome, detail, to_start, to_cancel = await _code_event(
                session, installation, repository_ext, delivery, event, action, payload
            )
        delivery_row = await session.get(GitDelivery, row_id)
        if delivery_row is not None:
            delivery_row.outcome = outcome
            delivery_row.detail = detail
            delivery_row.review_ids = [str(r) for r in to_start] or None
    if sync is not None and container.github is not None:
        try:
            async with transaction(container.session_factory) as session:
                fresh = await session.get(GitInstallation, sync.id, with_for_update=True)
                if fresh is not None and fresh.revoked_at is None:
                    await git_service.sync_installation(session, container.github, fresh)
        except GitHubError:
            detail += " (repository list will refresh on the next sync)"
    await git_service.cancel_reviews(container.workflows, to_cancel)
    await git_service.start_reviews(container.workflows, to_start)
    return {"outcome": outcome, "detail": detail, "reviews": [str(r) for r in to_start]}


def _installation_event(
    installation: GitInstallation, action: str | None, payload: dict[str, Any]
) -> tuple[str, str, GitInstallation | None]:
    now = datetime.now(UTC)
    data = payload.get("installation") or {}
    if action == "deleted":
        installation.revoked_at = now
        return "accepted", "the app was uninstalled; reviews stop", None
    if action == "suspend":
        installation.suspended_at = now
        return "accepted", "the installation was suspended; reviews stop", None
    if action == "unsuspend":
        installation.suspended_at = None
        return "accepted", "the installation was resumed", installation
    if action in {"new_permissions_accepted", "created"}:
        installation.permissions = dict(data.get("permissions") or installation.permissions)
        return "accepted", "permissions updated", installation
    return "ignored", f"installation {action} is not used", None


async def _cancel_for_installation(
    session: AsyncSession, installation: GitInstallation
) -> list[uuid.UUID]:
    connections = (
        select(GitConnection.id)
        .join(GitRepository, GitRepository.id == GitConnection.repository_id)
        .where(GitRepository.installation_id == installation.id)
    )
    return await git_service.request_cancel(session, CodeReview.connection_id.in_(connections))


async def _repository(
    session: AsyncSession, installation: GitInstallation, external: int
) -> GitRepository | None:
    return (
        await session.execute(
            select(GitRepository).where(
                GitRepository.installation_id == installation.id,
                GitRepository.external_id == external,
            )
        )
    ).scalar_one_or_none()


async def _code_event(
    session: AsyncSession,
    installation: GitInstallation,
    repository_ext: int,
    delivery: str,
    event: str,
    action: str | None,
    payload: dict[str, Any],
) -> tuple[str, str, list[uuid.UUID], list[uuid.UUID]]:
    if installation.revoked_at is not None or installation.suspended_at is not None:
        return "ignored", "the installation is not active", [], []
    repo = await _repository(session, installation, repository_ext)
    if repo is None or repo.removed_at is not None:
        return "ignored", "refactorX cannot read this repository", [], []
    connection = (
        await session.execute(select(GitConnection).where(GitConnection.repository_id == repo.id))
    ).scalar_one_or_none()
    if connection is None:
        return "ignored", "this repository is not connected to a project", [], []
    key = f"delivery-{delivery}"
    if event == "push":
        ref = str(payload.get("ref") or "")
        if ref != f"refs/heads/{repo.default_branch}":
            return "ignored", "only pushes to the default branch are reviewed", [], []
        if payload.get("deleted"):
            return "ignored", "the branch was deleted", [], []
        if not connection.review_pushes:
            return "ignored", "the project does not review pushes", [], []
        review = await git_service.create_review(
            session,
            connection,
            kind=CodeReviewKind.BRANCH.value,
            trigger=CodeReviewTrigger.PUSH.value,
            key=key,
            ref=repo.default_branch,
            delivery_id=delivery,
        )
        return "accepted", "default branch review queued", [review], []
    pull = payload.get("pull_request") or {}
    number = _int(pull.get("number") or payload.get("number"))
    if number is None:
        return "ignored", "no pull request number", [], []
    if action == "closed":
        canceled = await git_service.request_cancel(
            session,
            CodeReview.project_id == connection.project_id,
            CodeReview.kind == CodeReviewKind.PULL_REQUEST.value,
            CodeReview.pr_number == number,
        )
        return "accepted", "pull request closed; its reviews stop", [], canceled
    base_changed = action == "edited" and "base" in (payload.get("changes") or {})
    if action not in _PR_ACTIONS and not base_changed:
        return "ignored", f"pull_request {action} does not change the code", [], []
    if not connection.review_pull_requests:
        return "ignored", "the project does not review pull requests", [], []
    head_repo = _int(((pull.get("head") or {}).get("repo") or {}).get("id"))
    if head_repo != repository_ext and not connection.review_forks:
        return (
            "ignored",
            "pull requests from forks are reviewed only when a project admin allows it",
            [],
            [],
        )
    review = await git_service.create_review(
        session,
        connection,
        kind=CodeReviewKind.PULL_REQUEST.value,
        trigger=CodeReviewTrigger.PULL_REQUEST.value,
        key=key,
        pr_number=number,
        delivery_id=delivery,
    )
    return "accepted", "pull request review queued", [review], []
