"""Insights (docs/PRODUCT_SIMPLIFICATION.md): what to improve, by area, from the tools; and an
improvement plan from the advisor agent, checked against the same evidence.

The recommendations are computed on request from the newest reviewed upload, the tracked issues
and the NFR profile (the insight engine). The plan is an AI run of kind ``advisor`` whose
evidence pack is frozen at the request; its steps are kept only with valid citations and
numbers taken from the evidence (``crp_analysis.insights.advisor``).
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from fastapi import APIRouter
from sqlalchemy import select

from crp_analysis.ai.config import resolve
from crp_analysis.insights.advisor import ADVISOR_PROMPT_VERSION
from crp_analysis.insights.engine import ENGINE_VERSION, Insight, build, facts
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.routes.ai import ai_gate, run_limits, run_response
from crp_api.schemas import (
    AdvisorState,
    AiRunResponse,
    AreaHealthResponse,
    InsightIssueRef,
    InsightResponse,
    InsightsResponse,
    NfrBasis,
)
from crp_api.services import nfr
from crp_api.services.scope import get_scoped
from crp_core.db.models import AiRun, Project, ProjectAiPolicy
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


def _insight(insight: Insight) -> InsightResponse:
    return InsightResponse(
        id=insight.id,
        area=insight.area,
        kind=cast(Any, insight.kind),
        priority=cast(Any, insight.priority),
        title=insight.title,
        summary=insight.summary,
        why=insight.why,
        steps=list(insight.steps),
        questions=list(insight.questions),
        issue_count=len(insight.issues),
        issues=[
            InsightIssueRef(id=uuid.UUID(i.id), title=i.title, severity=i.severity, path=i.path)
            for i in insight.issues[:5]
        ],
        issue_ids=[uuid.UUID(i.id) for i in insight.issues[:MAX_FIX_ISSUES]],
    )


@router.get("/projects/{project_id}/insights", response_model=InsightsResponse, responses=_ERRORS)
async def get_insights(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> InsightsResponse:
    """What to improve, by area, with evidence and guided steps; and the latest advisor plan."""
    setup = resolve(container.settings)
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        gathered = await nfr.gather(session, project.id)
        report = build(gathered.assessment, gathered.issues, gathered.evidence)
        policy = await session.get(ProjectAiPolicy, project.id)
        latest = (
            await session.execute(
                select(AiRun)
                .where(AiRun.project_id == project.id, AiRun.kind == AiRunKind.ADVISOR.value)
                .order_by(AiRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        latest_response = run_response(latest, []) if latest else None
        member = principal.has_role(project.workspace_id, MembershipRole.MEMBER)
    enabled = bool(policy and policy.enabled and setup.available and setup.model)
    if not (policy and policy.enabled):
        reason: str | None = "AI is switched off for this project (Settings)."
    elif not setup.available:
        reason = setup.reason or "AI is not set up on this server."
    elif gathered.basis is None:
        reason = "Review an upload first; the advisor plans from its evidence."
    else:
        reason = None
    return InsightsResponse(
        project_id=project_id,
        engine=ENGINE_VERSION,
        basis=NfrBasis(
            snapshot_id=gathered.basis.snapshot_id,
            scan_id=gathered.basis.scan_id,
            reviewed_at=gathered.basis.reviewed_at,
        )
        if gathered.basis
        else None,
        areas=[
            AreaHealthResponse(
                id=a.area.id,
                name=a.area.name,
                state=cast(Any, a.state),
                insights=a.insights,
                questions=a.questions,
            )
            for a in report.areas
        ],
        recommendations=[_insight(i) for i in report.insights],
        advisor=AdvisorState(
            enabled=enabled,
            reason=reason,
            can_request=enabled and member and gathered.basis is not None,
            latest=latest_response,
        ),
    )


@router.post(
    "/projects/{project_id}/insights/plan",
    response_model=AiRunResponse,
    responses={**_ERRORS, 429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def request_plan(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> AiRunResponse:
    """Ask the advisor agent for an improvement plan (members; AI must be on for the project).
    The recommendations and facts it may use are frozen now; every step is checked later."""
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
        gathered = await nfr.gather(session, project.id)
        if gathered.basis is None:
            raise ApiError(409, "no_review", "Review an upload first; the advisor needs evidence")
        report = build(gathered.assessment, gathered.issues, gathered.evidence)
        context = {
            "engine": ENGINE_VERSION,
            "recommendations": [
                {
                    "id": i.id,
                    "area": i.area,
                    "priority": i.priority,
                    "title": i.title,
                    "summary": i.summary,
                }
                for i in report.insights
            ],
            "facts": facts(report, gathered.evidence),
            "targets": gathered.profile.values_for(
                (*gathered.profile.targets, "regulations", "platforms")
            ),
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
