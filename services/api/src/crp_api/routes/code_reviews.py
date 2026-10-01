"""A project's GitHub connection, its review and publication policy, and code reviews (P06).

Connecting and changing the policy is for project admins and is audited. Publication is off by
default. Starting or stopping a review is for members; reading is for viewers. Reviews resolve the
newest commit when they run (see ``crp_worker.git_review``).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    CodeReviewCreate,
    CodeReviewPage,
    CodeReviewResponse,
    GitConnectionCreate,
    GitConnectionResponse,
    GitConnectionUpdate,
)
from crp_api.services import git as git_service
from crp_api.services.scope import get_scoped
from crp_core.db.models import (
    CodeReview,
    GitConnection,
    GitConnectionEvent,
    GitInstallation,
    GitRepository,
    Project,
    Source,
)
from crp_core.db.session import transaction
from crp_core.domain.states import (
    CodeReviewKind,
    CodeReviewState,
    CodeReviewTrigger,
    MembershipRole,
    SourceMode,
)

router = APIRouter(tags=["code reviews"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_POLICY = (
    "review_pushes",
    "review_pull_requests",
    "review_forks",
    "publish_checks",
    "publish_pull_requests",
    "check_fail_threshold",
    "reconcile_days",
)


def _review(review: CodeReview, repository: str | None) -> CodeReviewResponse:
    return CodeReviewResponse(
        id=review.id,
        project_id=review.project_id,
        repository=repository,
        kind=review.kind,
        trigger=review.trigger,
        state=review.state,
        ref=review.ref,
        head_sha=review.head_sha,
        pr_number=review.pr_number,
        pr_title=review.pr_title,
        pr_author=review.pr_author,
        pr_url=review.pr_url,
        fork=review.fork,
        base_ref=review.base_ref,
        base_sha=review.base_sha,
        merge_base_sha=review.merge_base_sha,
        full=review.full,
        head_snapshot_id=review.head_snapshot_id,
        base_snapshot_id=review.base_snapshot_id,
        head_scan_id=review.head_scan_id,
        base_scan_id=review.base_scan_id,
        changes=review.changes,
        result=review.result,
        publish_state=review.publish_state,
        publish_error=review.publish_error,
        published_at=review.published_at,
        superseded_by=review.superseded_by,
        error_code=review.error_code,
        error_message=review.error_message,
        created_at=review.created_at,
        started_at=review.started_at,
        finished_at=review.finished_at,
    )


async def _connection_row(
    session: Any, project_id: uuid.UUID
) -> tuple[GitConnection, GitRepository, GitInstallation] | None:
    row = (
        await session.execute(
            select(GitConnection, GitRepository, GitInstallation)
            .join(GitRepository, GitRepository.id == GitConnection.repository_id)
            .join(GitInstallation, GitInstallation.id == GitRepository.installation_id)
            .where(GitConnection.project_id == project_id)
        )
    ).one_or_none()
    return (row[0], row[1], row[2]) if row is not None else None


def _connection(
    project: Project,
    row: tuple[GitConnection, GitRepository, GitInstallation] | None,
    can_edit: bool,
) -> GitConnectionResponse:
    if row is None:
        return GitConnectionResponse(connected=False, can_edit=can_edit)
    connection, repository, installation = row
    status, reason = git_service.connection_status(installation, repository)
    return GitConnectionResponse(
        connected=True,
        can_edit=can_edit,
        id=connection.id,
        status=status,
        status_reason=reason,
        repository=git_service.repository_response(repository, project.id, project.name),
        installation_account=installation.account_login,
        review_pushes=connection.review_pushes,
        review_pull_requests=connection.review_pull_requests,
        review_forks=connection.review_forks,
        publish_checks=connection.publish_checks,
        publish_pull_requests=connection.publish_pull_requests,
        check_fail_threshold=connection.check_fail_threshold,
        reconcile_days=connection.reconcile_days,
        last_full_review_at=connection.last_full_review_at,
        version=connection.version,
        created_at=connection.created_at,
    )


@router.get(
    "/projects/{project_id}/git-connection", response_model=GitConnectionResponse, responses=_ERRORS
)
async def get_connection(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> GitConnectionResponse:
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        can_edit = not principal.is_demo and principal.has_role(
            project.workspace_id, MembershipRole.ADMIN
        )
        return _connection(project, await _connection_row(session, project.id), can_edit)


@router.put(
    "/projects/{project_id}/git-connection",
    status_code=201,
    response_model=GitConnectionResponse,
    responses=_ERRORS,
)
async def connect_repository(
    project_id: uuid.UUID,
    body: GitConnectionCreate,
    principal: CurrentPrincipal,
    container: Container,
) -> GitConnectionResponse:
    """Connect a repository from a linked installation and start its first (full) review."""
    if principal.is_demo:
        raise ApiError(403, "demo_not_allowed", "The demo account cannot connect GitHub")
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.ADMIN,
            for_update=True,
        )
        if await _connection_row(session, project.id) is not None:
            raise ApiError(409, "already_connected", "This project already has a repository")
        repository = await session.get(GitRepository, body.repository_id)
        installation = (
            await session.get(GitInstallation, repository.installation_id) if repository else None
        )
        if (
            repository is None
            or installation is None
            or repository.workspace_id != project.workspace_id
        ):
            raise ApiError(404, "repository_not_found", "Repository not found in this workspace")
        status, reason = git_service.connection_status(installation, repository)
        if status != "active":
            raise ApiError(409, status, reason or "The repository is not available")
        taken = await session.scalar(
            select(GitConnection.project_id).where(GitConnection.repository_id == repository.id)
        )
        if taken is not None:
            raise ApiError(409, "repository_connected", "This repository is connected elsewhere")
        source = Source(
            workspace_id=project.workspace_id,
            project_id=project.id,
            mode=SourceMode.GITHUB.value,
            display_name=repository.full_name,
        )
        session.add(source)
        await session.flush()
        connection = GitConnection(
            workspace_id=project.workspace_id,
            project_id=project.id,
            repository_id=repository.id,
            source_id=source.id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(connection)
        await session.flush()
        session.add(
            GitConnectionEvent(
                project_id=project.id,
                actor_user_id=principal.user_id,
                kind="connected",
                changes={"repository": repository.full_name},
            )
        )
        review_id = await git_service.create_review(
            session,
            connection,
            kind=CodeReviewKind.BRANCH.value,
            trigger=CodeReviewTrigger.MANUAL.value,
            key=f"connect-{connection.id.hex}",
            ref=repository.default_branch,
            requested_by=principal.user_id,
            full=True,
        )
        await session.flush()
        response = _connection(project, await _connection_row(session, project.id), True)
    await git_service.start_reviews(container.workflows, [review_id])
    return response


@router.patch(
    "/projects/{project_id}/git-connection",
    response_model=GitConnectionResponse,
    responses=_ERRORS,
)
async def update_connection(
    project_id: uuid.UUID,
    body: GitConnectionUpdate,
    principal: CurrentPrincipal,
    container: Container,
) -> GitConnectionResponse:
    """Change what is reviewed and published (admins; versioned and audited)."""
    if principal.is_demo:
        raise ApiError(403, "demo_not_allowed", "The demo account cannot change GitHub settings")
    try:
        async with transaction(container.session_factory) as session:
            project = await get_scoped(
                session,
                principal,
                Project,
                project_id,
                not_found="project_not_found",
                required=MembershipRole.ADMIN,
            )
            row = await _connection_row(session, project.id)
            if row is None:
                raise ApiError(404, "not_connected", "This project has no repository connected")
            connection = row[0]
            if connection.version != body.version:
                raise ApiError(
                    409,
                    "version_conflict",
                    "The settings changed since you loaded them; reload and try again",
                    {"current_version": connection.version},
                )
            changes: dict[str, object] = {}
            for name in _POLICY:
                value = getattr(body, name)
                if value is None:
                    continue
                value = getattr(value, "value", value)
                if getattr(connection, name) != value:
                    changes[name] = [getattr(connection, name), value]
                    setattr(connection, name, value)
            if changes:
                connection.updated_by = principal.user_id
                session.add(
                    GitConnectionEvent(
                        project_id=project.id,
                        actor_user_id=principal.user_id,
                        kind="policy_changed",
                        changes=changes,
                    )
                )
            await session.flush()
            await session.refresh(connection)
            return _connection(project, await _connection_row(session, project.id), True)
    except StaleDataError as exc:
        raise ApiError(409, "version_conflict", "The settings changed meanwhile; reload") from exc


@router.delete("/projects/{project_id}/git-connection", status_code=204, responses=_ERRORS)
async def disconnect_repository(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> None:
    """Disconnect: reviews stop; earlier snapshots, scans and reviews stay as history."""
    if principal.is_demo:
        raise ApiError(403, "demo_not_allowed", "The demo account cannot change GitHub settings")
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.ADMIN,
        )
        row = await _connection_row(session, project.id)
        if row is None:
            raise ApiError(404, "not_connected", "This project has no repository connected")
        connection, repository, _ = row
        canceled = await git_service.request_cancel(
            session, CodeReview.connection_id == connection.id
        )
        session.add(
            GitConnectionEvent(
                project_id=project.id,
                actor_user_id=principal.user_id,
                kind="disconnected",
                changes={"repository": repository.full_name},
            )
        )
        await session.delete(connection)
    await git_service.cancel_reviews(container.workflows, canceled)


# -- reviews ---------------------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/code-reviews",
    status_code=202,
    response_model=CodeReviewResponse,
    responses=_ERRORS,
)
async def start_review(
    project_id: uuid.UUID,
    body: CodeReviewCreate,
    principal: CurrentPrincipal,
    container: Container,
) -> CodeReviewResponse:
    """Review the default branch's newest commit, or a pull request, now."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.MEMBER,
        )
        row = await _connection_row(session, project.id)
        if row is None:
            raise ApiError(409, "not_connected", "Connect a GitHub repository first")
        connection, repository, installation = row
        status, reason = git_service.connection_status(installation, repository)
        if status != "active":
            raise ApiError(409, status, reason or "The repository is not available")
        if body.kind is CodeReviewKind.PULL_REQUEST and body.pull_request is None:
            raise ApiError(422, "pull_request_required", "Give the pull request number")
        review_id = await git_service.create_review(
            session,
            connection,
            kind=body.kind.value,
            trigger=CodeReviewTrigger.MANUAL.value,
            key=f"manual-{uuid.uuid4().hex}",
            ref=repository.default_branch if body.kind is CodeReviewKind.BRANCH else None,
            pr_number=body.pull_request if body.kind is CodeReviewKind.PULL_REQUEST else None,
            requested_by=principal.user_id,
            full=body.full,
        )
        await session.flush()
        review = await session.get(CodeReview, review_id)
        if review is None:
            raise ApiError(404, "review_not_found", "Review not found")
        await session.refresh(review)
        response = _review(review, repository.full_name)
    problems = await git_service.start_reviews(container.workflows, [review_id])
    if problems:
        async with transaction(container.session_factory) as session:
            stored = await session.get(CodeReview, review_id, with_for_update=True)
            if stored is not None and stored.state == CodeReviewState.QUEUED.value:
                stored.state = CodeReviewState.FAILED.value
                stored.error_code = "workflow_unavailable"
                stored.error_message = "The review service is not running; try again shortly."
        raise ApiError(503, "workflow_unavailable", problems[0])
    return response


@router.get("/projects/{project_id}/code-reviews", response_model=CodeReviewPage, responses=_ERRORS)
async def list_reviews(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    kind: CodeReviewKind | None = None,
    pull_request: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> CodeReviewPage:
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        query = select(CodeReview).where(CodeReview.project_id == project.id)
        if kind is not None:
            query = query.where(CodeReview.kind == kind.value)
        if pull_request is not None:
            query = query.where(CodeReview.pr_number == pull_request)
        reviews = list(
            (
                await session.execute(query.order_by(CodeReview.created_at.desc()).limit(limit))
            ).scalars()
        )
        row = await _connection_row(session, project.id)
        repository = row[1].full_name if row else None
        return CodeReviewPage(items=[_review(r, repository) for r in reviews])


@router.get("/code-reviews/{review_id}", response_model=CodeReviewResponse, responses=_ERRORS)
async def get_review(
    review_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> CodeReviewResponse:
    async with transaction(container.session_factory) as session:
        review = await get_scoped(
            session, principal, CodeReview, review_id, not_found="review_not_found"
        )
        row = await _connection_row(session, review.project_id)
        return _review(review, row[1].full_name if row else None)


@router.post(
    "/code-reviews/{review_id}/cancel", response_model=CodeReviewResponse, responses=_ERRORS
)
async def cancel_review(
    review_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> CodeReviewResponse:
    async with transaction(container.session_factory) as session:
        review = await get_scoped(
            session,
            principal,
            CodeReview,
            review_id,
            not_found="review_not_found",
            required=MembershipRole.MEMBER,
            for_update=True,
        )
        canceled = await git_service.request_cancel(session, CodeReview.id == review.id)
        row = await _connection_row(session, review.project_id)
        await session.flush()
        await session.refresh(review)
        response = _review(review, row[1].full_name if row else None)
    await git_service.cancel_reviews(container.workflows, canceled)
    return response
