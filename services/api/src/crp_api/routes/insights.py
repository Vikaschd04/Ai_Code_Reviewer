"""Insights (ADR 0024): the NFR checkpoints of a project, from its newest reviewed upload, with
what to do about each; the team's "handled elsewhere" decisions; and an improvement plan from the
advisor agent, checked against the same evidence.

Checkpoints are computed on request (``crp_analysis.insights.engine``). The plan is an AI run of
kind ``advisor`` whose evidence pack is frozen at the request; its steps are kept only with valid
citations and numbers taken from the evidence (``crp_analysis.insights.advisor``).
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from crp_analysis.ai.config import resolve
from crp_analysis.insights import decisions
from crp_analysis.insights.advisor import ADVISOR_PROMPT_VERSION
from crp_analysis.insights.engine import (
    BY_ID,
    ENGINE_VERSION,
    MISSING_CAPABLE,
    CheckpointResult,
    facts,
)
from crp_analysis.nfr.signals import Evidence
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.auth.principal import Principal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.routes.ai import ai_gate, run_limits, run_response
from crp_api.schemas import (
    AdvisorState,
    AiRunResponse,
    AreaHealthResponse,
    CheckpointHandled,
    CheckpointResponse,
    EvidenceItem,
    EvidenceLocation,
    InsightIssueRef,
    InsightsBasis,
    InsightsResponse,
)
from crp_api.services import insights
from crp_api.services.scope import get_scoped
from crp_core.config import Settings
from crp_core.db.models import AiRun, NfrProfileVersion, Project, ProjectAiPolicy
from crp_core.db.session import transaction
from crp_core.domain.states import AiRunKind, AiRunState, MembershipRole
from crp_core.workflows.contracts import ai_run_workflow_id
from crp_core.workflows.gateway import WorkflowUnavailableError

router = APIRouter(tags=["insights"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
}
MAX_FIX_ISSUES = 50


def _evidence(item: Evidence) -> EvidenceItem:
    return EvidenceItem(
        signal=item.signal,
        label=item.label,
        count=item.count,
        locations=[EvidenceLocation(path=p, line=n, detail=d) for p, n, d in item.locations],
    )


def _checkpoint(result: CheckpointResult) -> CheckpointResponse:
    checkpoint = result.checkpoint
    return CheckpointResponse(
        id=checkpoint.id,
        area=checkpoint.area,
        title=checkpoint.title,
        status=cast(Any, result.status),
        priority=cast(Any, result.priority),
        summary=result.summary,
        why=checkpoint.why,
        steps=list(result.steps),
        issue_count=len(result.issues),
        issues=[
            InsightIssueRef(id=uuid.UUID(i.id), title=i.title, severity=i.severity, path=i.path)
            for i in result.issues[:5]
        ],
        issue_ids=[uuid.UUID(i.id) for i in result.issues[:MAX_FIX_ISSUES]],
        evidence=[_evidence(e) for e in result.evidence],
        handled_reason=result.handled_reason,
        can_mark_handled=result.status in {"missing", "handled"},
    )


async def _response(
    session: Any, settings: Settings, principal: Principal, project: Project
) -> InsightsResponse:
    setup = resolve(settings)
    gathered = await insights.gather(session, project.id)
    report = gathered.report()
    policy = await session.get(ProjectAiPolicy, project.id)
    latest = (
        await session.execute(
            select(AiRun)
            .where(AiRun.project_id == project.id, AiRun.kind == AiRunKind.ADVISOR.value)
            .order_by(AiRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    member = principal.has_role(project.workspace_id, MembershipRole.MEMBER)
    enabled = bool(policy and policy.enabled and setup.available and setup.model)
    if not (policy and policy.enabled):
        reason: str | None = "AI is switched off for this project (Settings)."
    elif not setup.available:
        reason = setup.reason or "AI is not set up on this server."
    elif gathered.basis is None:
        reason = "Review an upload first; the advisor plans from its evidence."
    elif not report.failing():
        reason = "No checkpoint needs work, so there is nothing to plan."
    else:
        reason = None
    return InsightsResponse(
        project_id=project.id,
        engine=ENGINE_VERSION,
        basis=InsightsBasis(
            snapshot_id=gathered.basis.snapshot_id,
            scan_id=gathered.basis.scan_id,
            reviewed_at=gathered.basis.reviewed_at,
        )
        if gathered.basis
        else None,
        areas=[
            AreaHealthResponse(
                id=a.area.id, name=a.area.name, state=cast(Any, a.state), counts=a.counts
            )
            for a in report.areas
        ],
        checkpoints=[_checkpoint(r) for r in report.checkpoints],
        not_checked=[_evidence(e) for e in gathered.evidence if e.kind == "context"],
        decisions_version=gathered.version,
        can_edit=member,
        advisor=AdvisorState(
            enabled=enabled,
            reason=reason,
            can_request=enabled and member and reason is None,
            latest=run_response(latest, []) if latest else None,
        ),
    )


@router.get("/projects/{project_id}/insights", response_model=InsightsResponse, responses=_ERRORS)
async def get_insights(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> InsightsResponse:
    """The project's NFR checkpoints by area, with evidence and how to resolve them; and the
    latest advisor plan."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        return await _response(session, container.settings, principal, project)


async def _decide(
    project_id: uuid.UUID,
    checkpoint_id: str,
    reason: str | None,
    principal: Principal,
    container: Container,
) -> InsightsResponse:
    if checkpoint_id not in BY_ID:
        raise ApiError(404, "checkpoint_not_found", "No such checkpoint")
    if checkpoint_id not in MISSING_CAPABLE:
        raise ApiError(
            422,
            "not_decidable",
            "Only checkpoints about a missing mechanism can be marked as handled elsewhere; "
            "resolve or triage the issues instead",
        )
    try:
        async with transaction(container.session_factory) as session:
            project = await get_scoped(
                session,
                principal,
                Project,
                project_id,
                not_found="project_not_found",
                required=MembershipRole.MEMBER,
                for_update=True,
            )
            version, handled = await insights.decisions(session, project.id)
            try:
                updated = decisions.with_handled(handled, checkpoint_id, reason)
            except decisions.DecisionError as exc:
                raise ApiError(422, "invalid_decision", str(exc)) from None
            if updated != handled:
                document = decisions.document(updated)
                session.add(
                    NfrProfileVersion(
                        workspace_id=project.workspace_id,
                        project_id=project.id,
                        version=version + 1,
                        document=document,
                        sha256=decisions.sha256(document),
                        note=f"{'handled' if reason else 'cleared'}: {checkpoint_id}",
                        created_by=principal.user_id,
                    )
                )
                await session.flush()
            return await _response(session, container.settings, principal, project)
    except IntegrityError:
        raise ApiError(
            409, "version_conflict", "Someone else changed the decisions; reload and try again"
        ) from None


@router.put(
    "/projects/{project_id}/insights/checkpoints/{checkpoint_id}/handled",
    response_model=InsightsResponse,
    responses={**_ERRORS, 422: {"model": ErrorResponse}},
)
async def mark_handled(
    project_id: uuid.UUID,
    checkpoint_id: str,
    body: CheckpointHandled,
    principal: CurrentPrincipal,
    container: Container,
) -> InsightsResponse:
    """Record that a missing mechanism is handled outside this code (members). Shown as the
    team's statement, never as detected."""
    return await _decide(project_id, checkpoint_id, body.reason, principal, container)


@router.delete(
    "/projects/{project_id}/insights/checkpoints/{checkpoint_id}/handled",
    response_model=InsightsResponse,
    responses={**_ERRORS, 422: {"model": ErrorResponse}},
)
async def clear_handled(
    project_id: uuid.UUID, checkpoint_id: str, principal: CurrentPrincipal, container: Container
) -> InsightsResponse:
    """Undo "handled elsewhere" for a checkpoint (members)."""
    return await _decide(project_id, checkpoint_id, None, principal, container)


@router.post(
    "/projects/{project_id}/insights/plan",
    response_model=AiRunResponse,
    responses={**_ERRORS, 429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def request_plan(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> AiRunResponse:
    """Ask the advisor agent for an improvement plan (members; AI must be on for the project).
    The checkpoints and facts it may use are frozen now; every step is checked later."""
    setup = resolve(container.settings)
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.MEMBER,
        )
        policy = await ai_gate(session, setup, project.id)
        gathered = await insights.gather(session, project.id)
        if gathered.basis is None:
            raise ApiError(409, "no_review", "Review an upload first; the advisor needs evidence")
        report = gathered.report()
        failing = report.failing()
        if not failing:
            raise ApiError(409, "nothing_to_plan", "No checkpoint needs work")
        context = {
            "engine": ENGINE_VERSION,
            "checkpoints": [
                {
                    "id": r.checkpoint.id,
                    "area": r.checkpoint.area,
                    "priority": r.priority,
                    "title": r.checkpoint.title,
                    "summary": r.summary,
                }
                for r in failing
            ],
            "facts": facts(report),
        }
        run = AiRun(
            workspace_id=project.workspace_id,
            project_id=project.id,
            snapshot_id=gathered.basis.snapshot_id,
            scan_id=gathered.basis.scan_id,
            kind=AiRunKind.ADVISOR.value,
            state=AiRunState.QUEUED.value,
            context=context,
            provider=setup.provider.value,
            model=setup.model or "",
            prompt_version=ADVISOR_PROMPT_VERSION,
            requested_by=principal.user_id,
            limits=run_limits(setup, policy),
        )
        session.add(run)
        await session.flush()
        run.workflow_id = ai_run_workflow_id(run.id)
        await session.refresh(run)
        run_id = run.id
        response = run_response(run, [])
    try:
        await container.workflows.start_ai_run(run_id)
    except WorkflowUnavailableError as exc:
        async with transaction(container.session_factory) as session:
            stored = await session.get(AiRun, run_id, with_for_update=True)
            if stored is not None and stored.state == AiRunState.QUEUED.value:
                stored.state = AiRunState.FAILED.value
                stored.error_code = "workflow_unavailable"
                stored.error_message = "The review service is not running; nothing was sent."
        raise ApiError(503, "workflow_unavailable", f"{exc}; try again shortly") from exc
    return response
