"""Shared GitHub connection helpers for routes and webhook handling (P06; ADR 0015)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.sources.github import GitHubClient, RepoAccess
from crp_api.schemas import GitInstallationResponse, GitRepositoryResponse
from crp_core.db.models import (
    CodeReview,
    GitConnection,
    GitInstallation,
    GitRepository,
    Project,
    Snapshot,
)
from crp_core.domain.states import CodeReviewState
from crp_core.workflows.gateway import WorkflowGateway, WorkflowUnavailableError

logger = logging.getLogger(__name__)

ACTIVE_REVIEWS = (
    CodeReviewState.QUEUED.value,
    CodeReviewState.CAPTURING.value,
    CodeReviewState.SCANNING.value,
    CodeReviewState.PUBLISHING.value,
)


async def repositories(
    session: AsyncSession, installation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[GitRepositoryResponse]]:
    rows = (
        await session.execute(
            select(GitRepository, GitConnection.project_id, Project.name)
            .outerjoin(GitConnection, GitConnection.repository_id == GitRepository.id)
            .outerjoin(Project, Project.id == GitConnection.project_id)
            .where(GitRepository.installation_id.in_(installation_ids))
            .order_by(GitRepository.full_name)
        )
    ).all()
    grouped: dict[uuid.UUID, list[GitRepositoryResponse]] = {i: [] for i in installation_ids}
    for repo, project_id, project_name in rows:
        grouped[repo.installation_id].append(repository_response(repo, project_id, project_name))
    return grouped


def repository_response(
    repo: GitRepository, project_id: uuid.UUID | None = None, project_name: str | None = None
) -> GitRepositoryResponse:
    return GitRepositoryResponse(
        id=repo.id,
        full_name=repo.full_name,
        default_branch=repo.default_branch,
        private=repo.private,
        archived=repo.archived,
        removed=repo.removed_at is not None,
        project_id=project_id,
        project_name=project_name,
    )


def installation_response(
    installation: GitInstallation, repos: list[GitRepositoryResponse]
) -> GitInstallationResponse:
    return GitInstallationResponse(
        id=installation.id,
        account=installation.account_login,
        account_type=installation.account_type,
        repository_selection=installation.repository_selection,
        suspended=installation.suspended_at is not None,
        revoked=installation.revoked_at is not None,
        linked_at=installation.created_at,
        synced_at=installation.synced_at,
        repositories=repos,
    )


async def store_repositories(
    session: AsyncSession, installation: GitInstallation, listed: list[dict[str, Any]]
) -> None:
    """Upsert the repositories GitHub lists for an installation; mark missing ones removed."""
    now = datetime.now(UTC)
    seen: set[int] = set()
    existing = {
        r.external_id: r
        for r in (
            await session.execute(
                select(GitRepository).where(GitRepository.installation_id == installation.id)
            )
        ).scalars()
    }
    for item in listed:
        external = int(item["id"])
        seen.add(external)
        repo = existing.get(external)
        if repo is None:
            session.add(
                GitRepository(
                    workspace_id=installation.workspace_id,
                    installation_id=installation.id,
                    external_id=external,
                    full_name=str(item["full_name"])[:255],
                    default_branch=str(item.get("default_branch") or "main")[:255],
                    private=bool(item.get("private", True)),
                    archived=bool(item.get("archived", False)),
                )
            )
        else:
            repo.full_name = str(item["full_name"])[:255]
            repo.default_branch = str(item.get("default_branch") or repo.default_branch)[:255]
            repo.private = bool(item.get("private", repo.private))
            repo.archived = bool(item.get("archived", repo.archived))
            repo.removed_at = None
    for external, repo in existing.items():
        if external not in seen and repo.removed_at is None:
            repo.removed_at = now
    installation.synced_at = now


async def sync_installation(
    session: AsyncSession, client: GitHubClient, installation: GitInstallation
) -> None:
    listed = await client.installation_repositories(installation.external_id)
    await store_repositories(session, installation, listed)


async def create_review(
    session: AsyncSession,
    connection: GitConnection,
    *,
    kind: str,
    trigger: str,
    key: str,
    ref: str | None = None,
    pr_number: int | None = None,
    requested_by: uuid.UUID | None = None,
    delivery_id: str | None = None,
    full: bool = False,
) -> uuid.UUID:
    """Insert a QUEUED review (idempotent per project and key) and return its id."""
    review_id = (
        await session.execute(
            insert(CodeReview)
            .values(
                id=uuid.uuid4(),
                workspace_id=connection.workspace_id,
                project_id=connection.project_id,
                connection_id=connection.id,
                kind=kind,
                trigger=trigger,
                state=CodeReviewState.QUEUED.value,
                idempotency_key=key,
                ref=ref,
                pr_number=pr_number,
                requested_by=requested_by,
                delivery_id=delivery_id,
                full=full,
            )
            .on_conflict_do_nothing(
                index_elements=[CodeReview.project_id, CodeReview.idempotency_key]
            )
            .returning(CodeReview.id)
        )
    ).scalar_one_or_none()
    if review_id is None:
        review_id = (
            await session.execute(
                select(CodeReview.id).where(
                    CodeReview.project_id == connection.project_id,
                    CodeReview.idempotency_key == key,
                )
            )
        ).scalar_one()
    review = await session.get(CodeReview, review_id)
    if review is not None and review.workflow_id is None:
        review.workflow_id = f"crp-review-{review_id.hex}"
    return review_id


async def request_cancel(session: AsyncSession, *conditions: Any) -> list[uuid.UUID]:
    """Record a cancel request on matching active reviews; callers cancel their workflows."""
    now = datetime.now(UTC)
    reviews = list(
        (
            await session.execute(
                select(CodeReview).where(CodeReview.state.in_(ACTIVE_REVIEWS), *conditions)
            )
        ).scalars()
    )
    for review in reviews:
        review.cancel_requested_at = review.cancel_requested_at or now
    return [review.id for review in reviews]


async def start_reviews(workflows: WorkflowGateway, review_ids: list[uuid.UUID]) -> list[str]:
    """Start review workflows; failures are logged and returned (reviews stay QUEUED)."""
    problems = []
    for review_id in review_ids:
        try:
            await workflows.start_git_review(review_id)
        except WorkflowUnavailableError as exc:
            logger.warning("could not start a code review", extra={"review_id": str(review_id)})
            problems.append(str(exc))
    return problems


async def cancel_reviews(workflows: WorkflowGateway, review_ids: list[uuid.UUID]) -> None:
    for review_id in review_ids:
        try:
            await workflows.cancel_git_review(review_id)
        except WorkflowUnavailableError:
            logger.warning("could not cancel a code review", extra={"review_id": str(review_id)})


def connection_status(
    installation: GitInstallation, repository: GitRepository
) -> tuple[str, str | None]:
    if installation.revoked_at is not None:
        return "installation_revoked", "The GitHub App was uninstalled from this account."
    if installation.suspended_at is not None:
        return "installation_suspended", "The GitHub App installation is suspended on GitHub."
    if repository.removed_at is not None:
        return "access_removed", "refactorX no longer has access to this repository on GitHub."
    return "active", None


@dataclass(frozen=True, slots=True)
class PublishTarget:
    """Where a pull request for changes to one reviewed GitHub commit would go."""

    access: RepoAccess
    branch: str
    base_sha: str
    tree_sha: str
    executable: frozenset[str]


async def publish_target(
    session: AsyncSession, project_id: uuid.UUID, snapshot: Snapshot
) -> tuple[PublishTarget | None, str | None]:
    """The pull request target for changes to ``snapshot``, or why there is none (plain text).

    (None, None) means the code did not come from GitHub, so pull requests do not apply.
    """
    if snapshot.git_provider is None or not snapshot.git_commit:
        return None, None
    row = (
        await session.execute(
            select(GitConnection, GitRepository, GitInstallation)
            .join(GitRepository, GitRepository.id == GitConnection.repository_id)
            .join(GitInstallation, GitInstallation.id == GitRepository.installation_id)
            .where(GitConnection.project_id == project_id)
        )
    ).one_or_none()
    if row is None:
        return None, "The repository is no longer connected to this project."
    connection, repository, installation = row
    status, reason = connection_status(installation, repository)
    if status != "active":
        return None, reason
    if snapshot.git_repository and snapshot.git_repository != repository.full_name:
        renamed = await session.scalar(
            select(GitRepository.id).where(GitRepository.full_name == snapshot.git_repository)
        )
        if renamed is not None and renamed != repository.id:
            return None, "This code belongs to a different repository than the connected one."
    if not connection.publish_pull_requests:
        return None, "A project admin has not allowed refactorX to open pull requests."
    fork = await session.scalar(
        select(CodeReview.id).where(
            CodeReview.head_snapshot_id == snapshot.id, CodeReview.fork.is_(True)
        )
    )
    if fork is not None:
        return None, "This code comes from a fork; refactorX cannot add a branch there."
    if not snapshot.git_ref:
        return None, "The reviewed commit is not on a known branch."
    capture = snapshot.git_capture or {}
    listed = capture.get("executable") if isinstance(capture, dict) else None
    return (
        PublishTarget(
            RepoAccess(installation.external_id, repository.external_id, repository.full_name),
            snapshot.git_ref,
            snapshot.git_commit,
            snapshot.git_tree_sha or "",
            frozenset(str(p) for p in listed) if isinstance(listed, list) else frozenset(),
        ),
        None,
    )
