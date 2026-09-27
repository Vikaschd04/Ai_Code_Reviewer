"""Durable issues: filtered listing, detail with audit trail, and optimistic triage updates.

Triage may set OPEN, TRIAGED, ACCEPTED_RISK (reason + future expiry within a year) or
FALSE_POSITIVE (reason). RESOLVED is only reached through a verified absence and FIX_PROPOSED
through the fix workflow; resolved issues reopen automatically when reported again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm.exc import StaleDataError

from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    IssueDetailResponse,
    IssueEventResponse,
    IssuePage,
    IssueResponse,
    IssueTriage,
)
from crp_api.services.scope import decode_offset, encode_offset, get_scoped
from crp_core.db.models import Finding, Issue, IssueEvent, Project
from crp_core.db.session import transaction
from crp_core.domain.states import FindingStatus, MembershipRole, Severity

router = APIRouter(tags=["issues"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
}
MAX_EXCEPTION_DAYS = 365
_SEVERITY_RANK = {s.value: i for i, s in enumerate(Severity)}
_EXCEPTIONS = {FindingStatus.ACCEPTED_RISK.value, FindingStatus.FALSE_POSITIVE.value}


def issue_response(issue: Issue, now: datetime | None = None) -> IssueResponse:
    now = now or datetime.now(UTC)
    return IssueResponse(
        id=issue.id,
        project_id=issue.project_id,
        fingerprint=issue.fingerprint,
        engine=issue.engine,
        rule_id=issue.rule_id,
        path=issue.path,
        title=issue.title,
        severity=issue.severity,
        category=issue.category,
        status=issue.status,
        recheck_state=issue.recheck_state,
        recheck_reason=issue.recheck_reason,
        owner=issue.owner,
        exception_reason=issue.exception_reason,
        exception_expires_at=issue.exception_expires_at,
        exception_expired=issue.status == FindingStatus.ACCEPTED_RISK.value
        and issue.exception_expires_at is not None
        and issue.exception_expires_at <= now,
        first_seen_scan_id=issue.first_seen_scan_id,
        last_seen_scan_id=issue.last_seen_scan_id,
        last_evaluated_scan_id=issue.last_evaluated_scan_id,
        last_seen_engine_version=issue.last_seen_engine_version,
        last_seen_at=issue.last_seen_at,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
        version=issue.version,
    )


@router.get("/projects/{project_id}/issues", response_model=IssuePage, responses=_ERRORS)
async def list_issues(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    status: Annotated[list[str] | None, Query()] = None,
    recheck_state: Annotated[list[str] | None, Query()] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    engine: Annotated[str | None, Query(max_length=32)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> IssuePage:
    offset = decode_offset(cursor)
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        base = select(Issue).where(Issue.project_id == project.id)
        by_status = dict(
            (
                await session.execute(
                    select(Issue.status, func.count())
                    .where(Issue.project_id == project.id)
                    .group_by(Issue.status)
                )
            ).all()
        )
        by_recheck = dict(
            (
                await session.execute(
                    select(Issue.recheck_state, func.count())
                    .where(Issue.project_id == project.id)
                    .group_by(Issue.recheck_state)
                )
            ).all()
        )
        query = base
        if status:
            query = query.where(Issue.status.in_(status))
        if recheck_state:
            query = query.where(Issue.recheck_state.in_(recheck_state))
        if severity:
            query = query.where(Issue.severity.in_(severity))
        if engine:
            query = query.where(Issue.engine == engine)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(
                Issue.path.ilike(f"%{escaped}%", escape="\\")
                | Issue.title.ilike(f"%{escaped}%", escape="\\")
            )
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rank = case(_SEVERITY_RANK, value=Issue.severity, else_=9)
        rows = list(
            (
                await session.execute(
                    query.order_by(rank, Issue.path, Issue.id).offset(offset).limit(limit + 1)
                )
            ).scalars()
        )
    now = datetime.now(UTC)
    return IssuePage(
        items=[issue_response(i, now) for i in rows[:limit]],
        next_cursor=encode_offset(offset + limit) if len(rows) > limit else None,
        total=total,
        by_status={str(k): int(v) for k, v in by_status.items()},
        by_recheck={str(k): int(v) for k, v in by_recheck.items()},
    )


async def _detail(session: Any, issue: Issue) -> IssueDetailResponse:
    events = (
        await session.execute(
            select(IssueEvent)
            .where(IssueEvent.issue_id == issue.id)
            .order_by(IssueEvent.id.desc())
            .limit(100)
        )
    ).scalars()
    latest = await session.scalar(
        select(Finding.id)
        .where(Finding.issue_id == issue.id)
        .order_by(Finding.created_at.desc())
        .limit(1)
    )
    return IssueDetailResponse(
        issue=issue_response(issue),
        events=[
            IssueEventResponse(
                id=e.id,
                kind=e.kind,
                actor_kind=e.actor_kind,
                actor_user_id=e.actor_user_id,
                scan_id=e.scan_id,
                changes=e.changes,
                reason=e.reason,
                created_at=e.created_at,
            )
            for e in events
        ],
        latest_finding_id=latest,
    )


@router.get("/issues/{issue_id}", response_model=IssueDetailResponse, responses=_ERRORS)
async def get_issue(
    issue_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> IssueDetailResponse:
    async with transaction(container.session_factory) as session:
        issue = await get_scoped(session, principal, Issue, issue_id, not_found="issue_not_found")
        return await _detail(session, issue)


def _validate(body: IssueTriage, issue: Issue, now: datetime) -> None:
    status = body.status
    if status is None:
        return
    if issue.status == FindingStatus.RESOLVED.value and status != issue.status:
        raise ApiError(
            409,
            "issue_resolved",
            "Resolved issues are closed by a verified absence and reopen if reported again",
        )
    if issue.status == FindingStatus.FIX_PROPOSED.value and status != issue.status:
        raise ApiError(409, "fix_in_progress", "A fix proposal owns this issue's status")
    if status in _EXCEPTIONS and not body.reason:
        raise ApiError(422, "reason_required", f"{status} requires a reason")
    if status == FindingStatus.ACCEPTED_RISK.value:
        if body.expires_at is None:
            raise ApiError(422, "expiry_required", "ACCEPTED_RISK requires an expiry date")
        expires = body.expires_at if body.expires_at.tzinfo else body.expires_at.replace(tzinfo=UTC)
        if expires <= now:
            raise ApiError(422, "expiry_in_past", "The exception expiry must be in the future")
        if expires > now + timedelta(days=MAX_EXCEPTION_DAYS):
            raise ApiError(
                422, "expiry_too_far", f"Exceptions may last at most {MAX_EXCEPTION_DAYS} days"
            )


@router.patch(
    "/issues/{issue_id}",
    response_model=IssueDetailResponse,
    responses={**_ERRORS, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def triage_issue(
    issue_id: uuid.UUID, body: IssueTriage, principal: CurrentPrincipal, container: Container
) -> IssueDetailResponse:
    now = datetime.now(UTC)
    try:
        async with transaction(container.session_factory) as session:
            issue = await get_scoped(
                session,
                principal,
                Issue,
                issue_id,
                not_found="issue_not_found",
                required=MembershipRole.MEMBER,
                for_update=True,
            )
            if issue.version != body.version:
                raise ApiError(
                    409,
                    "version_conflict",
                    f"Issue changed (version {issue.version}); reload and retry",
                )
            _validate(body, issue, now)
            changes: dict[str, object] = {}
            if body.owner is not None and (body.owner or None) != issue.owner:
                changes["owner"] = [issue.owner, body.owner or None]
                issue.owner = body.owner or None
            if body.status is not None and body.status != issue.status:
                changes["status"] = [issue.status, body.status]
            if body.status is not None:
                if body.status in _EXCEPTIONS:
                    expires = (
                        body.expires_at.replace(tzinfo=body.expires_at.tzinfo or UTC)
                        if body.expires_at and body.status == FindingStatus.ACCEPTED_RISK.value
                        else None
                    )
                    if body.reason != issue.exception_reason:
                        changes["exception_reason"] = [issue.exception_reason, body.reason]
                    if expires != issue.exception_expires_at:
                        changes["exception_expires_at"] = [
                            issue.exception_expires_at.isoformat()
                            if issue.exception_expires_at
                            else None,
                            expires.isoformat() if expires else None,
                        ]
                    issue.exception_reason = body.reason
                    issue.exception_expires_at = expires
                elif issue.exception_reason or issue.exception_expires_at:
                    changes["exception_reason"] = [issue.exception_reason, None]
                    issue.exception_reason = None
                    issue.exception_expires_at = None
                issue.status = body.status
            if changes:
                session.add(
                    IssueEvent(
                        issue_id=issue.id,
                        actor_kind="user",
                        actor_user_id=principal.user_id,
                        kind="triaged",
                        changes=changes,
                        reason=body.reason,
                    )
                )
                await session.flush()
                await session.refresh(issue)  # server-generated updated_at
            return await _detail(session, issue)
    except StaleDataError as exc:
        raise ApiError(409, "version_conflict", "Issue changed concurrently; reload") from exc
