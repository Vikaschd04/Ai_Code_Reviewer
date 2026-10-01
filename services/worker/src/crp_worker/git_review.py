"""Code reviews of connected GitHub repositories (P06; ADR 0015).

A review follows these steps:
1. It resolves the newest commit of its branch or pull request when it runs, so late, duplicate or
   out-of-order events converge on the same commit. A review of an older commit is canceled as
   superseded; a second review of a commit already being reviewed is skipped.
2. It captures the commit as a snapshot checked against the commit's tree
   (``crp_analysis.sources.capture``).
3. It scans the snapshot, reusing per-file results for unchanged files.
4. It compares the result with the merge base (pull requests) or with the branch's previously
   reviewed commit.
5. When the project allows it, it publishes one check and one summary comment.

Only reviews of the default branch change the project's issues. Pull request and merge-base scans
never do (``lifecycle._applicable``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ChildWorkflowError, is_cancelled_exception
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    import uuid

    from sqlalchemy import and_, func, or_, select, update
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.orm import aliased

    from crp_analysis.inventory import build_inventory
    from crp_analysis.lifecycle import Prior, RunView, classify_absence
    from crp_analysis.manifest import blob_key
    from crp_analysis.normalize import evidence_line
    from crp_analysis.sources import publication
    from crp_analysis.sources.capture import reconcile_with_tree
    from crp_analysis.sources.changes import (
        ChangeSet,
        FindingRef,
        diff_findings,
        diff_manifests,
        impacted_files,
    )
    from crp_analysis.sources.github import (
        GitHubAccessError,
        GitHubClient,
        GitHubError,
        GitHubNotFoundError,
        GitHubUnavailableError,
        RepoAccess,
        resolve_github,
    )
    from crp_analysis.zip_intake import GitArchiveOptions, IntakeRejectedError, process_zip
    from crp_core.artifacts import ArtifactKey, ArtifactStore
    from crp_core.config import Settings
    from crp_core.db.models import (
        CodeReview,
        EngineRun,
        FileCoverage,
        FileEntry,
        Finding,
        GitConnection,
        GitInstallation,
        GitRepository,
        GraphBuild,
        GraphEdge,
        GraphNode,
        Intake,
        Scan,
        Snapshot,
    )
    from crp_core.db.session import transaction
    from crp_core.domain.states import (
        CacheMode,
        CaptureStatus,
        CodeReviewKind,
        CodeReviewState,
        CodeReviewTrigger,
        FileDisposition,
        GitProvider,
        IntakeState,
        PublishState,
        RecheckState,
        ScanMode,
        ScanState,
        SourceMode,
    )
    from crp_core.workflows.contracts import (
        EXTRACTOR_NAMES,
        GIT_REVIEW_WORKFLOW_NAME,
        SCAN_POLICY,
        SCAN_WORKFLOW_NAME,
        GitReviewFinalize,
        GitReviewInput,
        GitReviewPlan,
        GitReviewResult,
        ScanWorkflowInput,
        ScanWorkflowResult,
        scan_workflow_id,
    )
    from crp_worker.intake import freeze_snapshot, limits_from
    from crp_worker.progress import heartbeat

logger = logging.getLogger(__name__)

ACTIVE = frozenset(
    {
        CodeReviewState.QUEUED.value,
        CodeReviewState.CAPTURING.value,
        CodeReviewState.SCANNING.value,
        CodeReviewState.PUBLISHING.value,
    }
)
_CLAIMED = frozenset(
    {
        CodeReviewState.CAPTURING.value,
        CodeReviewState.SCANNING.value,
        CodeReviewState.PUBLISHING.value,
        CodeReviewState.SUCCEEDED.value,
        CodeReviewState.PARTIAL.value,
    }
)
_CLAIMED_ACTIVE = frozenset(
    {
        CodeReviewState.CAPTURING.value,
        CodeReviewState.SCANNING.value,
        CodeReviewState.PUBLISHING.value,
    }
)
_AUTOMATIC = frozenset({CodeReviewTrigger.PUSH.value, CodeReviewTrigger.PULL_REQUEST.value})
_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_NEW_ITEMS = 50
_FIXED_ITEMS = 20
_RENAME_TEXT_FILES = 200

CancelReview = Callable[[UUID], Awaitable[None]]


class ReviewStop(Exception):  # control flow: the review ends with this outcome
    def __init__(self, state: CodeReviewState, code: str | None, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class _Context:
    review_id: UUID
    workspace_id: UUID
    project_id: UUID
    kind: str
    trigger: str
    ref: str | None
    pr_number: int | None
    full: bool
    requested_by: UUID | None
    connection_id: UUID
    source_id: UUID
    access: RepoAccess
    default_branch: str
    review_forks: bool
    reconcile_days: int
    last_full_review_at: datetime | None


def details_link(settings: Settings, review_id: UUID) -> str | None:
    """Link back to the review in refactorX (hosted deployments only: a public web origin)."""
    if not settings.hosted or not settings.allowed_web_origins:
        return None
    return f"{settings.allowed_web_origins[0].rstrip('/')}/#/reviews/{review_id}"


class GitReviewActivities:
    def __init__(
        self,
        settings: Settings,
        store: ArtifactStore,
        sessions: async_sessionmaker[AsyncSession],
        *,
        cancel_review: CancelReview | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._sessions = sessions
        self._cancel_review = cancel_review
        self._client: GitHubClient | None = None

    def _github(self) -> GitHubClient:
        if self._client is None:
            setup = resolve_github(self._settings)
            if not setup.app_ready:
                raise ReviewStop(
                    CodeReviewState.FAILED,
                    "github_not_configured",
                    setup.reason or "GitHub is not set up on this server.",
                )
            self._client = GitHubClient(setup)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    # -- state helpers ------------------------------------------------------------------------

    async def _end(
        self,
        review_id: UUID,
        state: CodeReviewState,
        *,
        code: str | None = None,
        message: str | None = None,
        superseded_by: UUID | None = None,
    ) -> GitReviewResult:
        async with transaction(self._sessions) as session:
            review = await session.get(CodeReview, review_id, with_for_update=True)
            if review is None:
                return GitReviewResult(review_id=review_id, state="MISSING")
            if CodeReviewState(review.state).is_terminal:
                return GitReviewResult(review_id=review_id, state=review.state)
            review.state = state.value
            review.finished_at = datetime.now(UTC)
            if code is not None:
                review.error_code, review.error_message = code, message
            elif message is not None:
                review.error_message = message
            if superseded_by is not None:
                review.superseded_by = superseded_by
            if review.publish_state is None:
                review.publish_state = PublishState.SKIPPED.value
        logger.info("code review ended", extra={"review_id": str(review_id), "state": state.value})
        return GitReviewResult(review_id=review_id, state=state.value)

    async def _load(self, review_id: UUID) -> tuple[_Context | None, CodeReview | None]:
        async with transaction(self._sessions) as session:
            review = await session.get(CodeReview, review_id)
            if review is None:
                return None, None
            session.expunge(review)
            if review.connection_id is None:
                raise ReviewStop(
                    CodeReviewState.FAILED,
                    "repository_disconnected",
                    "The repository was disconnected from this project.",
                )
            row = (
                await session.execute(
                    select(GitConnection, GitRepository, GitInstallation)
                    .join(GitRepository, GitRepository.id == GitConnection.repository_id)
                    .join(GitInstallation, GitInstallation.id == GitRepository.installation_id)
                    .where(GitConnection.id == review.connection_id)
                )
            ).one_or_none()
            if row is None:
                raise ReviewStop(
                    CodeReviewState.FAILED,
                    "repository_disconnected",
                    "The repository was disconnected from this project.",
                )
            connection, repository, installation = row
            if installation.revoked_at is not None:
                raise ReviewStop(
                    CodeReviewState.FAILED,
                    "installation_revoked",
                    "The GitHub App was uninstalled; reconnect it to review this repository.",
                )
            if installation.suspended_at is not None:
                raise ReviewStop(
                    CodeReviewState.FAILED,
                    "installation_suspended",
                    "The GitHub App installation is suspended on GitHub.",
                )
            if repository.removed_at is not None:
                raise ReviewStop(
                    CodeReviewState.FAILED,
                    "repository_access_removed",
                    "refactorX no longer has access to this repository on GitHub.",
                )
            context = _Context(
                review_id=review.id,
                workspace_id=review.workspace_id,
                project_id=review.project_id,
                kind=review.kind,
                trigger=review.trigger,
                ref=review.ref,
                pr_number=review.pr_number,
                full=review.full,
                requested_by=review.requested_by,
                connection_id=connection.id,
                source_id=connection.source_id,
                access=RepoAccess(
                    installation.external_id, repository.external_id, repository.full_name
                ),
                default_branch=repository.default_branch,
                review_forks=connection.review_forks,
                reconcile_days=connection.reconcile_days,
                last_full_review_at=connection.last_full_review_at,
            )
            return context, review

    async def _record_access_loss(self, context: _Context, exc: GitHubAccessError) -> None:
        """Mirror what GitHub said about the installation so later reviews fail fast."""
        now = datetime.now(UTC)
        async with transaction(self._sessions) as session:
            installation = (
                await session.execute(
                    select(GitInstallation).where(
                        GitInstallation.provider == GitProvider.GITHUB.value,
                        GitInstallation.external_id == context.access.installation_id,
                    )
                )
            ).scalar_one_or_none()
            if installation is None:
                return
            if exc.code == "installation_not_found":
                installation.revoked_at = installation.revoked_at or now
            elif exc.code == "installation_suspended":
                installation.suspended_at = installation.suspended_at or now
        if self._client is not None:
            self._client.forget_tokens(context.access.installation_id)

    # -- prepare --------------------------------------------------------------------------------

    @activity.defn(name="git.prepare")
    async def prepare(self, payload: GitReviewInput) -> GitReviewPlan:
        review_id = payload.review_id
        context: _Context | None = None
        try:
            context, review = await self._load(review_id)
            if context is None or review is None:
                return GitReviewPlan(review_id=review_id, terminal=True)
            if CodeReviewState(review.state).is_terminal:
                return GitReviewPlan(review_id=review_id, terminal=True)
            if review.cancel_requested_at is not None:
                await self._end(
                    review_id,
                    CodeReviewState.SUPERSEDED
                    if review.superseded_by
                    else CodeReviewState.CANCELED,
                )
                return GitReviewPlan(review_id=review_id, terminal=True)
            heartbeat("resolving the commit")
            target = await self._resolve(context)
            plan = await self._claim(context, target)
            if plan is not None:
                return plan
            heartbeat("capturing the commit")
            head_snapshot = await self._snapshot(context, target["head_sha"], target["head_ref"])
            base_snapshot: UUID | None = None
            if target.get("merge_base_sha"):
                base_snapshot = await self._snapshot(
                    context, str(target["merge_base_sha"]), target.get("base_ref")
                )
            return await self._scans(context, target, head_snapshot, base_snapshot)
        except ReviewStop as stop:
            await self._end(review_id, stop.state, code=stop.code, message=stop.message)
            return GitReviewPlan(review_id=review_id, terminal=True)
        except GitHubAccessError as exc:
            if context is not None:
                await self._record_access_loss(context, exc)
            await self._end(review_id, CodeReviewState.FAILED, code=exc.code, message=exc.message)
            return GitReviewPlan(review_id=review_id, terminal=True)
        except GitHubUnavailableError as exc:
            await self._end(
                review_id,
                CodeReviewState.FAILED,
                code="github_unavailable",
                message=f"GitHub could not be reached; run the review again later. ({exc.message})",
            )
            return GitReviewPlan(review_id=review_id, terminal=True)
        except IntakeRejectedError as exc:
            await self._end(
                review_id, CodeReviewState.FAILED, code="capture_rejected", message=exc.message
            )
            return GitReviewPlan(review_id=review_id, terminal=True)
        except GitHubError as exc:
            await self._end(review_id, CodeReviewState.FAILED, code=exc.code, message=exc.message)
            return GitReviewPlan(review_id=review_id, terminal=True)

    async def _resolve(self, context: _Context) -> dict[str, Any]:
        github = self._github()
        if context.kind == CodeReviewKind.BRANCH.value:
            branch = context.ref or context.default_branch
            try:
                head = await github.branch_head(context.access, branch)
            except GitHubNotFoundError as exc:
                raise ReviewStop(
                    CodeReviewState.SKIPPED,
                    "branch_not_found",
                    f"The branch {branch} no longer exists.",
                ) from exc
            return {"head_sha": head, "head_ref": branch}
        number = context.pr_number or 0
        try:
            pull = await github.pull_request(context.access, number)
        except GitHubNotFoundError as exc:
            raise ReviewStop(
                CodeReviewState.SKIPPED,
                "pull_request_not_found",
                f"Pull request #{number} was not found.",
            ) from exc
        if pull.state != "open":
            raise ReviewStop(
                CodeReviewState.SKIPPED,
                "pull_request_closed",
                f"Pull request #{number} is {pull.state}.",
            )
        if (
            pull.fork
            and not context.review_forks
            and context.trigger != CodeReviewTrigger.MANUAL.value
        ):
            raise ReviewStop(
                CodeReviewState.SKIPPED,
                "fork_not_reviewed",
                "This pull request comes from a fork. The project reviews forks only when a "
                "project admin allows it, or when someone starts the review by hand.",
            )
        merge_base = await github.merge_base(context.access, pull.base_sha, pull.head_sha)
        return {
            "head_sha": pull.head_sha,
            "head_ref": pull.head_ref,
            "base_ref": pull.base_ref,
            "base_sha": pull.base_sha,
            "merge_base_sha": merge_base,
            "title": pull.title,
            "author": pull.author,
            "url": pull.url,
            "fork": pull.fork,
        }

    def _same_target(self, context: _Context) -> Any:
        if context.kind == CodeReviewKind.BRANCH.value:
            return and_(
                CodeReview.kind == CodeReviewKind.BRANCH.value,
                CodeReview.ref == (context.ref or context.default_branch),
            )
        return and_(
            CodeReview.kind == CodeReviewKind.PULL_REQUEST.value,
            CodeReview.pr_number == context.pr_number,
        )

    async def _claim(self, context: _Context, target: dict[str, Any]) -> GitReviewPlan | None:
        """Decide under the project's connection lock: duplicate, superseding or go ahead."""
        stale: list[UUID] = []
        async with transaction(self._sessions) as session:
            await session.execute(
                select(GitConnection.id)
                .where(GitConnection.id == context.connection_id)
                .with_for_update()
            )
            review = await session.get(CodeReview, context.review_id, with_for_update=True)
            if review is None or CodeReviewState(review.state).is_terminal:
                return GitReviewPlan(review_id=context.review_id, terminal=True)
            same = [
                CodeReview.project_id == context.project_id,
                CodeReview.id != context.review_id,
                self._same_target(context),
            ]
            if context.trigger in _AUTOMATIC:
                duplicate = await session.scalar(
                    select(CodeReview.id)
                    .where(
                        *same,
                        CodeReview.head_sha == target["head_sha"],
                        CodeReview.merge_base_sha.is_not_distinct_from(
                            target.get("merge_base_sha")
                        ),
                        CodeReview.state.in_(_CLAIMED),
                    )
                    .limit(1)
                )
                if duplicate is not None:
                    review.state = CodeReviewState.SKIPPED.value
                    review.error_code = "already_reviewed"
                    review.error_message = "This commit is already reviewed or being reviewed."
                    review.head_sha = target["head_sha"]
                    review.finished_at = datetime.now(UTC)
                    review.publish_state = PublishState.SKIPPED.value
                    return GitReviewPlan(review_id=context.review_id, terminal=True)
            stale = list(
                (
                    await session.execute(
                        select(CodeReview.id).where(
                            *same,
                            # Only reviews that already claimed an older commit (or another merge
                            # base). A QUEUED review has not looked yet and may be for a newer push.
                            CodeReview.state.in_(_CLAIMED_ACTIVE),
                            or_(
                                CodeReview.head_sha != target["head_sha"],
                                CodeReview.merge_base_sha.is_distinct_from(
                                    target.get("merge_base_sha")
                                ),
                            ),
                        )
                    )
                ).scalars()
            )
            now = datetime.now(UTC)
            for other_id in stale:
                other = await session.get(CodeReview, other_id, with_for_update=True)
                if other is not None:
                    other.cancel_requested_at = other.cancel_requested_at or now
                    other.superseded_by = context.review_id
            review.state = CodeReviewState.CAPTURING.value
            review.started_at = review.started_at or now
            review.head_sha = target["head_sha"]
            review.ref = target.get("head_ref") or review.ref
            review.base_ref = target.get("base_ref")
            review.base_sha = target.get("base_sha")
            review.merge_base_sha = target.get("merge_base_sha")
            if target.get("title") is not None:
                review.pr_title = str(target["title"])[:300]
                review.pr_author = str(target["author"]) if target.get("author") else None
                review.pr_url = str(target.get("url") or "")[:500] or None
                review.fork = bool(target.get("fork"))
            due = (
                context.last_full_review_at is None
                or context.last_full_review_at < now - timedelta(days=context.reconcile_days)
            )
            if context.kind == CodeReviewKind.BRANCH.value and due:
                review.full = True
        for other_id in stale:
            if self._cancel_review is not None:
                try:
                    await self._cancel_review(other_id)
                except Exception:  # the stale review ends SUPERSEDED when it next checks in
                    logger.warning("could not cancel a superseded review", exc_info=True)
        return None

    async def _snapshot(self, context: _Context, sha: str, ref: str | None) -> UUID:
        """Reuse this project's snapshot of the commit, or capture it."""
        async with transaction(self._sessions) as session:
            existing = await session.scalar(
                select(Snapshot.id)
                .where(
                    Snapshot.project_id == context.project_id,
                    Snapshot.git_commit == sha,
                    Snapshot.git_provider == GitProvider.GITHUB.value,
                    Snapshot.capture_status == CaptureStatus.FROZEN.value,
                )
                .order_by(Snapshot.frozen_at.desc())
                .limit(1)
            )
            if existing is not None:
                return existing
            intake = Intake(
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                source_id=context.source_id,
                mode=SourceMode.GITHUB.value,
                state=IntakeState.VALIDATING.value,
                created_by=context.requested_by,
                expires_at=datetime.now(UTC)
                + timedelta(seconds=self._settings.intake_expiry_seconds),
            )
            session.add(intake)
            await session.flush()
            intake_id = intake.id
        github = self._github()
        limits = limits_from(self._settings)
        work = Path(self._settings.work_root)
        await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix="crp-git-", suffix=".zip", dir=work)
        archive = Path(name)
        try:
            with os.fdopen(handle, "wb") as sink:  # sequential writes of the download stream
                size = await github.download_archive(
                    context.access, sha, sink, max_bytes=limits.max_archive_bytes
                )
            heartbeat("checking the capture")

            def validate() -> Any:
                with archive.open("rb") as source:
                    return process_zip(
                        source,
                        archive_bytes=size,
                        store=self._store,
                        limits=limits,
                        git=GitArchiveOptions(),
                    )

            try:
                outcome = await asyncio.to_thread(validate)
                tree_sha = await github.commit_tree(context.access, sha)
                entries, truncated = await github.tree(context.access, tree_sha)

                async def fetch(blob_sha: str) -> bytes:
                    heartbeat("fetching files left out of the archive")
                    return await github.blob(
                        context.access, blob_sha, max_bytes=limits.max_text_file_bytes
                    )

                outcome, report = await reconcile_with_tree(
                    outcome,
                    entries,
                    truncated=truncated,
                    fetch=fetch,
                    store=self._store,
                    limits=limits,
                    max_fetches=self._settings.github_max_blob_fetches,
                )
            except IntakeRejectedError as exc:
                async with transaction(self._sessions) as session:
                    row = await session.get(Intake, intake_id, with_for_update=True)
                    if row is not None:
                        row.state = IntakeState.REJECTED.value
                        row.error_code, row.error_message = exc.code, exc.message
                        row.error_details = exc.details
                        row.finalized_at = datetime.now(UTC)
                raise
        finally:
            await asyncio.to_thread(archive.unlink, missing_ok=True)
        inventory = build_inventory(outcome.entries, outcome.inventory_texts)
        async with transaction(self._sessions) as session:
            row = await session.get(Intake, intake_id, with_for_update=True)
            if row is None:
                raise RuntimeError("capture intake disappeared")
            row.archive_bytes = size
            return await freeze_snapshot(
                session,
                self._store,
                row,
                outcome,
                inventory,
                git={
                    "commit": sha,
                    "ref": ref,
                    "provider": GitProvider.GITHUB.value,
                    "repository": context.access.full_name,
                    "tree": tree_sha,
                    "capture": report,
                },
            )

    async def _new_scan(
        self,
        session: AsyncSession,
        context: _Context,
        snapshot: UUID,
        mode: ScanMode,
        key: str,
        full: bool,
    ) -> UUID:
        scan_id = (
            await session.execute(
                insert(Scan)
                .values(
                    id=uuid.uuid4(),
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    snapshot_id=snapshot,
                    mode=mode.value,
                    policy_version=SCAN_POLICY,
                    idempotency_key=key,
                    state=ScanState.QUEUED.value,
                    cache_mode=(CacheMode.REFRESH if full else CacheMode.USE).value,
                    requested_by=context.requested_by,
                )
                .on_conflict_do_nothing(index_elements=[Scan.project_id, Scan.idempotency_key])
                .returning(Scan.id)
            )
        ).scalar_one_or_none()
        if scan_id is None:
            scan_id = (
                await session.execute(
                    select(Scan.id).where(
                        Scan.project_id == context.project_id, Scan.idempotency_key == key
                    )
                )
            ).scalar_one()
        await session.execute(
            update(Scan).where(Scan.id == scan_id).values(workflow_id=scan_workflow_id(scan_id))
        )
        return scan_id

    async def _scans(
        self,
        context: _Context,
        target: dict[str, Any],
        head_snapshot: UUID,
        base_snapshot: UUID | None,
    ) -> GitReviewPlan:
        runs: list[UUID] = []
        async with transaction(self._sessions) as session:
            review = await session.get(CodeReview, context.review_id, with_for_update=True)
            if review is None or CodeReviewState(review.state).is_terminal:
                return GitReviewPlan(review_id=context.review_id, terminal=True)
            if review.cancel_requested_at is not None:
                return GitReviewPlan(review_id=context.review_id)  # complete() ends it
            base_scan: UUID | None = None
            if base_snapshot is not None:
                base_scan = await session.scalar(
                    select(Scan.id)
                    .where(
                        Scan.snapshot_id == base_snapshot,
                        Scan.state.in_([ScanState.SUCCEEDED.value, ScanState.PARTIAL.value]),
                    )
                    .order_by(Scan.created_at.desc())
                    .limit(1)
                )
                if base_scan is None:
                    base_scan = await self._new_scan(
                        session,
                        context,
                        base_snapshot,
                        ScanMode.REFERENCE,
                        f"review-{context.review_id.hex}-base",
                        full=False,
                    )
                    runs.append(base_scan)
            elif context.kind == CodeReviewKind.BRANCH.value:
                previous = (
                    await session.execute(
                        select(
                            CodeReview.head_snapshot_id,
                            CodeReview.head_scan_id,
                            CodeReview.head_sha,
                        )
                        .where(
                            CodeReview.project_id == context.project_id,
                            CodeReview.id != context.review_id,
                            self._same_target(context),
                            CodeReview.state.in_(
                                [CodeReviewState.SUCCEEDED.value, CodeReviewState.PARTIAL.value]
                            ),
                            CodeReview.head_sha != target["head_sha"],
                        )
                        .order_by(CodeReview.finished_at.desc())
                        .limit(1)
                    )
                ).one_or_none()
                if previous is not None:
                    base_snapshot, base_scan = previous[0], previous[1]
                    review.base_sha = previous[2]
            mode = (
                ScanMode.BASELINE
                if context.kind == CodeReviewKind.BRANCH.value
                and (review.ref or context.default_branch) == context.default_branch
                else (
                    ScanMode.PULL_REQUEST
                    if context.kind == CodeReviewKind.PULL_REQUEST.value
                    else ScanMode.REFERENCE
                )
            )
            head_scan = await self._new_scan(
                session,
                context,
                head_snapshot,
                mode,
                f"review-{context.review_id.hex}-head",
                full=review.full,
            )
            runs.append(head_scan)
            review.head_snapshot_id = head_snapshot
            review.base_snapshot_id = base_snapshot
            review.head_scan_id = head_scan
            review.base_scan_id = base_scan
            review.state = CodeReviewState.SCANNING.value
            states = dict(
                (await session.execute(select(Scan.id, Scan.state).where(Scan.id.in_(runs)))).all()
            )
        pending = [s for s in runs if not ScanState(states.get(s, "QUEUED")).is_terminal]
        return GitReviewPlan(review_id=context.review_id, scan_ids=pending)

    # -- complete -------------------------------------------------------------------------------

    @activity.defn(name="git.complete")
    async def complete(self, payload: GitReviewInput) -> GitReviewResult:
        review_id = payload.review_id
        async with transaction(self._sessions) as session:
            review = await session.get(CodeReview, review_id)
            if review is None:
                return GitReviewResult(review_id=review_id, state="MISSING")
            if CodeReviewState(review.state).is_terminal:
                return GitReviewResult(review_id=review_id, state=review.state)
            session.expunge(review)
        if review.cancel_requested_at is not None:
            return await self._end(
                review_id,
                CodeReviewState.SUPERSEDED if review.superseded_by else CodeReviewState.CANCELED,
            )
        if review.head_scan_id is None or review.head_snapshot_id is None:
            return await self._end(
                review_id, CodeReviewState.FAILED, code="no_scan", message="No scan was started."
            )
        heartbeat("comparing results")
        result = await self._compare(review)
        state = CodeReviewState(result["state"])
        async with transaction(self._sessions) as session:
            row = await session.get(CodeReview, review_id, with_for_update=True)
            if row is None or CodeReviewState(row.state).is_terminal:
                return GitReviewResult(review_id=review_id, state=row.state if row else "MISSING")
            connection = (
                await session.get(GitConnection, row.connection_id) if row.connection_id else None
            )
            row.changes = result.pop("changes", None)
            row.result = result
            publish = (
                connection is not None
                and connection.publish_checks
                and row.cancel_requested_at is None
            )
            if (
                connection is not None
                and row.full
                and state in {CodeReviewState.SUCCEEDED, CodeReviewState.PARTIAL}
            ):
                # Core UPDATE: bookkeeping must not bump the policy version admins edit against.
                await session.execute(
                    update(GitConnection)
                    .where(GitConnection.id == connection.id)
                    .values(last_full_review_at=datetime.now(UTC))
                )
            if publish:
                row.state = CodeReviewState.PUBLISHING.value
            else:
                row.state = state.value
                row.finished_at = datetime.now(UTC)
                row.publish_state = PublishState.OFF.value
                if state is CodeReviewState.FAILED:
                    row.error_code = "scan_failed"
                    row.error_message = str(result.get("message") or "The scan did not complete.")
            return GitReviewResult(review_id=review_id, state=row.state)

    async def _compare(self, review: CodeReview) -> dict[str, Any]:
        async with transaction(self._sessions) as session:
            head = await session.get(Scan, review.head_scan_id)
            base = await session.get(Scan, review.base_scan_id) if review.base_scan_id else None
            if head is None:
                return {"state": CodeReviewState.FAILED.value, "message": "The scan was deleted."}
            head_state = ScanState(head.state)
            runs = list(
                (
                    await session.execute(select(EngineRun).where(EngineRun.scan_id == head.id))
                ).scalars()
            )
            incomplete = sorted(
                r.engine
                for r in runs
                if r.engine not in EXTRACTOR_NAMES
                and r.state not in {"SUCCEEDED", "NOT_APPLICABLE"}
            )
            cache = {
                "hits": sum(r.cache_hits or 0 for r in runs),
                "misses": sum(r.cache_misses or 0 for r in runs),
            }
            if head_state not in {ScanState.SUCCEEDED, ScanState.PARTIAL}:
                return {
                    "state": CodeReviewState.FAILED.value,
                    "message": head.error_message or f"The scan ended {head_state.value}.",
                    "scan_state": head_state.value,
                }
            head_files = await self._manifest(session, review.head_snapshot_id)
            changes: ChangeSet | None = None
            impacted: set[str] = set()
            if review.base_snapshot_id is not None:
                base_files = await self._manifest(session, review.base_snapshot_id)
                changes = diff_manifests(base_files, head_files)
                impacted = impacted_files(
                    changes.touched, await self._dependencies(session, review.head_snapshot_id)
                )
            head_findings = await self._findings(session, head.id)
            base_ok = base is not None and ScanState(base.state) in {
                ScanState.SUCCEEDED,
                ScanState.PARTIAL,
            }
            base_findings = await self._findings(session, base.id) if base_ok and base else []
            renames = changes.renames if changes is not None else {}
            if renames:
                head_findings = await self._with_text(
                    session, head_findings, set(renames), review.head_snapshot_id
                )
                base_findings = await self._with_text(
                    session, base_findings, set(renames.values()), review.base_snapshot_id
                )
            diff = diff_findings(base_findings, head_findings, renames)
            fixed, not_rechecked = await self._classify_absent(
                session, diff.absent, head, base, renames
            )
        partial = head_state is ScanState.PARTIAL or (
            review.base_scan_id is not None and not base_ok
        )
        new_sorted = sorted(
            diff.new, key=lambda f: (_RANK.get(f.severity, 9), f.path, f.start_line or 0)
        )
        by_severity: dict[str, int] = {}
        for finding in diff.new:
            by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        compared = None
        if review.kind == CodeReviewKind.PULL_REQUEST.value and review.merge_base_sha:
            branch = review.base_ref or "the base branch"
            compared = f"the merge base {review.merge_base_sha[:7]} of {branch}"
        elif review.base_sha:
            compared = f"the last reviewed commit {review.base_sha[:7]}"
        return {
            "state": (CodeReviewState.PARTIAL if partial else CodeReviewState.SUCCEEDED).value,
            "scan_state": head_state.value,
            "compared_with": compared,
            "new": len(diff.new),
            "unchanged": len(diff.unchanged),
            "fixed": len(fixed),
            "not_rechecked": not_rechecked,
            "by_severity": by_severity,
            "new_items": [self._item(f) for f in new_sorted[:_NEW_ITEMS]],
            "fixed_items": [self._item(f) for f in fixed[:_FIXED_ITEMS]],
            "incomplete": incomplete,
            "base_missing": review.base_scan_id is not None and not base_ok,
            "cache": cache,
            "changes": changes.as_dict(impacted) if changes is not None else None,
        }

    @staticmethod
    def _item(finding: FindingRef) -> dict[str, Any]:
        return {
            "finding_id": finding.id,
            "severity": finding.severity,
            "title": finding.title,
            "path": finding.path,
            "line": finding.start_line,
            "engine": finding.engine,
            "rule_id": finding.rule_id,
        }

    @staticmethod
    async def _manifest(session: AsyncSession, snapshot_id: UUID | None) -> dict[str, str]:
        rows = await session.execute(
            select(FileEntry.path, FileEntry.blob_sha256).where(
                FileEntry.snapshot_id == snapshot_id,
                FileEntry.disposition == FileDisposition.ANALYZABLE.value,
            )
        )
        return {path: sha for path, sha in rows.all() if sha}

    @staticmethod
    async def _dependencies(
        session: AsyncSession, snapshot_id: UUID | None
    ) -> list[tuple[str, str]]:
        source_node, target_node = aliased(GraphNode), aliased(GraphNode)
        source_file, target_file = aliased(FileEntry), aliased(FileEntry)
        rows = await session.execute(
            select(source_file.path, target_file.path)
            .select_from(GraphEdge)
            .join(
                GraphBuild,
                and_(GraphBuild.id == GraphEdge.build_id, GraphBuild.is_current.is_(True)),
            )
            .join(source_node, source_node.id == GraphEdge.source_node_id)
            .join(target_node, target_node.id == GraphEdge.target_node_id)
            .join(source_file, source_file.id == source_node.file_entry_id)
            .join(target_file, target_file.id == target_node.file_entry_id)
            .where(GraphEdge.snapshot_id == snapshot_id, source_file.path != target_file.path)
            .distinct()
            .limit(50_000)
        )
        return [(a, b) for a, b in rows.all()]

    @staticmethod
    async def _findings(session: AsyncSession, scan_id: UUID) -> list[FindingRef]:
        rows = await session.execute(
            select(Finding, FileEntry.path)
            .join(FileEntry, FileEntry.id == Finding.file_entry_id)
            .where(Finding.scan_id == scan_id)
        )
        return [
            FindingRef(
                str(f.id),
                f.fingerprint,
                f.engine,
                f.rule_id,
                path,
                f.start_line,
                f.severity,
                f.title,
            )
            for f, path in rows.all()
        ]

    async def _with_text(
        self,
        session: AsyncSession,
        findings: list[FindingRef],
        paths: set[str],
        snapshot_id: UUID | None,
    ) -> list[FindingRef]:
        """Add evidence-line text to findings in renamed files (for rename-aware matching)."""
        wanted = sorted({f.path for f in findings if f.path in paths and f.start_line})[
            :_RENAME_TEXT_FILES
        ]
        if not wanted:
            return findings
        blobs = dict(
            (
                await session.execute(
                    select(FileEntry.path, FileEntry.blob_sha256).where(
                        FileEntry.snapshot_id == snapshot_id, FileEntry.path.in_(wanted)
                    )
                )
            ).all()
        )
        texts: dict[str, str] = {}
        for path, sha in blobs.items():
            if sha:
                data = await asyncio.to_thread(
                    self._store.read_bytes,
                    ArtifactKey(blob_key(sha)),
                    max_bytes=self._settings.intake_max_text_file_bytes,
                )
                texts[path] = data.decode("utf-8", errors="replace")
        return [
            FindingRef(
                f.id,
                f.fingerprint,
                f.engine,
                f.rule_id,
                f.path,
                f.start_line,
                f.severity,
                f.title,
                evidence_line(texts[f.path], f.start_line)
                if f.path in texts and f.start_line
                else None,
            )
            for f in findings
        ]

    @staticmethod
    async def _classify_absent(
        session: AsyncSession,
        absent: tuple[FindingRef, ...],
        head: Scan,
        base: Scan | None,
        renames: dict[str, str],
    ) -> tuple[list[FindingRef], int]:
        if not absent or base is None:
            return [], len(absent)
        head_runs = {
            r.engine: r
            for r in (
                await session.execute(select(EngineRun).where(EngineRun.scan_id == head.id))
            ).scalars()
        }
        base_runs = {
            r.engine: r
            for r in (
                await session.execute(select(EngineRun).where(EngineRun.scan_id == base.id))
            ).scalars()
        }
        entries = dict(
            (
                await session.execute(
                    select(FileEntry.path, FileEntry.id).where(
                        FileEntry.snapshot_id == head.snapshot_id,
                        FileEntry.disposition == FileDisposition.ANALYZABLE.value,
                    )
                )
            ).all()
        )
        coverage = {
            (run_id, entry_id): outcome
            for run_id, entry_id, outcome in (
                await session.execute(
                    select(
                        FileCoverage.engine_run_id, FileCoverage.file_entry_id, FileCoverage.outcome
                    ).where(FileCoverage.engine_run_id.in_([r.id for r in head_runs.values()]))
                )
            ).all()
        }
        moved_to = {old: new for new, old in renames.items()}
        fixed: list[FindingRef] = []
        for finding in absent:
            path = moved_to.get(finding.path, finding.path)
            run = head_runs.get(finding.engine)
            prior_run = base_runs.get(finding.engine)
            entry_id = entries.get(path)
            view = (
                RunView(
                    finding.engine,
                    run.state,
                    run.engine_version,
                    run.ruleset_sha256,
                    frozenset(run.enabled_rules) if run.enabled_rules is not None else None,
                )
                if run is not None
                else None
            )
            verdict = classify_absence(
                Prior(
                    finding.engine,
                    finding.rule_id,
                    path,
                    prior_run.engine_version if prior_run else None,
                    prior_run.ruleset_sha256 if prior_run else None,
                ),
                view,
                file_present=entry_id is not None,
                file_outcome=coverage.get((run.id, entry_id))
                if run is not None and entry_id
                else None,
            )
            if verdict.state is RecheckState.VERIFIED_ABSENT:
                fixed.append(finding)
        return fixed, len(absent) - len(fixed)

    # -- publish --------------------------------------------------------------------------------

    @activity.defn(name="git.publish")
    async def publish(self, payload: GitReviewInput) -> GitReviewResult:
        """Post the check and summary comment; a failure here never fails the review."""
        review_id = payload.review_id
        async with transaction(self._sessions) as session:
            review = await session.get(CodeReview, review_id)
            if review is None:
                return GitReviewResult(review_id=review_id, state="MISSING")
            if review.state != CodeReviewState.PUBLISHING.value:
                return GitReviewResult(review_id=review_id, state=review.state)
            session.expunge(review)
            connection = (
                await session.get(GitConnection, review.connection_id)
                if review.connection_id
                else None
            )
            threshold = connection.check_fail_threshold if connection else "never"
            previous_comment = None
            if review.pr_number is not None:
                previous_comment = await session.scalar(
                    select(CodeReview.comment_id)
                    .where(
                        CodeReview.project_id == review.project_id,
                        CodeReview.pr_number == review.pr_number,
                        CodeReview.comment_id.is_not(None),
                    )
                    .order_by(CodeReview.published_at.desc())
                    .limit(1)
                )
        result = dict(review.result or {})
        final = CodeReviewState(str(result.get("state", CodeReviewState.PARTIAL.value)))
        info = {
            "review_id": review.id,
            "head_sha": review.head_sha,
            "pr_number": review.pr_number,
        }
        link = details_link(self._settings, review.id)
        publish_state, error, check_id, comment_id = (
            PublishState.PUBLISHED,
            None,
            review.check_run_id,
            None,
        )
        try:
            context, _ = await self._load(review_id)
        except ReviewStop as stop:
            return await self._published(
                review_id, final, PublishState.FAILED, stop.message, check_id, None
            )
        if context is None:
            return GitReviewResult(review_id=review_id, state="MISSING")
        try:
            github = self._github()
            check = publication.check_run_payload(info, result, threshold, link)
            if check_id is not None:
                await github.update_check_run(context.access, check_id, check)
            else:
                check_id = await github.create_check_run(context.access, check)
            if review.pr_number is not None:
                comment_id = await github.upsert_comment(
                    context.access,
                    review.pr_number,
                    publication.comment_body(info, result, link),
                    previous_comment,
                )
        except ReviewStop as stop:
            publish_state, error = PublishState.FAILED, stop.message
        except GitHubError as exc:
            publish_state, error = PublishState.FAILED, exc.message
            logger.warning(
                "publishing failed", extra={"review_id": str(review_id), "code": exc.code}
            )
        return await self._published(review_id, final, publish_state, error, check_id, comment_id)

    async def _published(
        self,
        review_id: UUID,
        final: CodeReviewState,
        publish_state: PublishState,
        error: str | None,
        check_id: int | None,
        comment_id: int | None,
    ) -> GitReviewResult:
        async with transaction(self._sessions) as session:
            row = await session.get(CodeReview, review_id, with_for_update=True)
            if row is None:
                return GitReviewResult(review_id=review_id, state="MISSING")
            row.publish_state = publish_state.value
            row.publish_error = error
            row.check_run_id = check_id
            row.comment_id = comment_id
            if publish_state is PublishState.PUBLISHED:
                row.published_at = datetime.now(UTC)
            if not CodeReviewState(row.state).is_terminal:
                row.state = final.value
                row.finished_at = datetime.now(UTC)
            return GitReviewResult(review_id=review_id, state=row.state)

    # -- finalize -------------------------------------------------------------------------------

    @activity.defn(name="git.finalize")
    async def finalize(self, payload: GitReviewFinalize) -> GitReviewResult:
        async with transaction(self._sessions) as session:
            review = await session.get(CodeReview, payload.review_id)
            superseded = review is not None and review.superseded_by is not None
            head_scan = review.head_scan_id if review is not None else None
        if payload.canceled:
            return await self._end(
                payload.review_id,
                CodeReviewState.SUPERSEDED if superseded else CodeReviewState.CANCELED,
            )
        message = "The review was interrupted; run it again."
        if head_scan is not None:
            async with transaction(self._sessions) as session:
                scan = await session.get(Scan, head_scan)
                if scan is not None and scan.error_message:
                    message = scan.error_message
        return await self._end(
            payload.review_id, CodeReviewState.FAILED, code="interrupted", message=message
        )

    def all(self) -> list[object]:
        return [self.prepare, self.complete, self.publish, self.finalize]


async def reconcile_due(
    sessions: async_sessionmaker[AsyncSession], start: Callable[[UUID], Awaitable[Any]]
) -> list[UUID]:
    """Queue a full review of the default branch for connections whose last one is too old."""
    now = datetime.now(UTC)
    created: list[UUID] = []
    async with transaction(sessions) as session:
        rows = (
            await session.execute(
                select(GitConnection, GitRepository)
                .join(GitRepository, GitRepository.id == GitConnection.repository_id)
                .join(GitInstallation, GitInstallation.id == GitRepository.installation_id)
                .where(
                    GitConnection.review_pushes.is_(True),
                    GitRepository.removed_at.is_(None),
                    GitInstallation.revoked_at.is_(None),
                    GitInstallation.suspended_at.is_(None),
                    or_(
                        GitConnection.last_full_review_at.is_(None),
                        GitConnection.last_full_review_at
                        < now - func.make_interval(0, 0, 0, GitConnection.reconcile_days),
                    ),
                )
            )
        ).all()
        for connection, repository in rows:
            active = await session.scalar(
                select(CodeReview.id).where(
                    CodeReview.connection_id == connection.id,
                    CodeReview.kind == CodeReviewKind.BRANCH.value,
                    CodeReview.state.in_(ACTIVE),
                )
            )
            if active is not None:
                continue
            key = f"reconcile-{connection.id.hex}-{now:%Y%m%d%H}"
            review_id = (
                await session.execute(
                    insert(CodeReview)
                    .values(
                        id=uuid.uuid4(),
                        workspace_id=connection.workspace_id,
                        project_id=connection.project_id,
                        connection_id=connection.id,
                        kind=CodeReviewKind.BRANCH.value,
                        trigger=CodeReviewTrigger.RECONCILE.value,
                        state=CodeReviewState.QUEUED.value,
                        idempotency_key=key,
                        ref=repository.default_branch,
                        full=True,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[CodeReview.project_id, CodeReview.idempotency_key]
                    )
                    .returning(CodeReview.id)
                )
            ).scalar_one_or_none()
            if review_id is not None:
                created.append(review_id)
    for review_id in created:
        try:
            await start(review_id)
        except Exception:
            logger.warning("could not start a scheduled full review", exc_info=True)
    return created


@workflow.defn(name=GIT_REVIEW_WORKFLOW_NAME)
class GitReviewWorkflow:
    @workflow.run
    async def run(self, payload: GitReviewInput) -> GitReviewResult:
        try:
            plan: GitReviewPlan = await workflow.execute_activity(
                "git.prepare",
                payload,
                result_type=GitReviewPlan,
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(minutes=5),
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)),
            )
            if plan.terminal:
                return GitReviewResult(review_id=payload.review_id, state="TERMINAL")
            for scan_id in plan.scan_ids:
                await workflow.execute_child_workflow(
                    SCAN_WORKFLOW_NAME,
                    ScanWorkflowInput(scan_id=scan_id),
                    id=scan_workflow_id(scan_id),
                    result_type=ScanWorkflowResult,
                )
            result: GitReviewResult = await workflow.execute_activity(
                "git.complete",
                payload,
                result_type=GitReviewResult,
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if result.state == CodeReviewState.PUBLISHING.value:
                result = await workflow.execute_activity(
                    "git.publish",
                    payload,
                    result_type=GitReviewResult,
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
        except (asyncio.CancelledError, ActivityError, ChildWorkflowError) as exc:
            canceled = is_cancelled_exception(exc)
            final: GitReviewResult = await workflow.execute_activity(
                "git.finalize",
                GitReviewFinalize(
                    review_id=payload.review_id, canceled=canceled, interrupted=not canceled
                ),
                result_type=GitReviewResult,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            return final
        return result
