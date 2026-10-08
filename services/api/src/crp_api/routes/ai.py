"""AI review (P03; ADR 0012): server status, per-project source-disclosure policy, runs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.ai.config import AiSetup, resolve
from crp_analysis.ai.export import build_ai_export, build_ai_sarif
from crp_analysis.ai.prompts import PROMPT_VERSION
from crp_api import __version__
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    AiAnchorResponse,
    AiAnswerResponse,
    AiFindingResponse,
    AiFixCandidateResponse,
    AiFixResult,
    AiLimits,
    AiMonthUsage,
    AiRunCreate,
    AiRunPage,
    AiRunResponse,
    AiStatus,
    AiStepResponse,
    AiUsageResponse,
    ProjectAiPolicyResponse,
    ProjectAiPolicyUpdate,
)
from crp_api.services.scope import get_scoped
from crp_core.db.ai_usage import month_usage
from crp_core.db.models import (
    AiFinding,
    AiPolicyEvent,
    AiRun,
    FileEntry,
    Finding,
    Project,
    ProjectAiPolicy,
    Scan,
    Snapshot,
)
from crp_core.db.session import transaction
from crp_core.domain.states import (
    AiRunKind,
    AiRunState,
    CaptureStatus,
    FileDisposition,
    MembershipRole,
)
from crp_core.workflows.contracts import ai_run_workflow_id
from crp_core.workflows.gateway import WorkflowUnavailableError

router = APIRouter(tags=["ai"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
}


@router.get("/ai/status", response_model=AiStatus, responses=_ERRORS)
async def ai_status(principal: CurrentPrincipal, container: Container) -> AiStatus:
    """Whether AI review can run on this server, its limits and this month's usage."""
    setup = resolve(container.settings)
    async with transaction(container.session_factory) as session:
        usage = await month_usage(session)
    return AiStatus(
        available=setup.available,
        provider=setup.provider.value,
        model=setup.model,
        reason=setup.reason,
        admin_hint=setup.admin_hint if principal.is_operator else None,
        prices_configured=setup.prices.known,
        limits=AiLimits(
            max_model_calls=setup.limits.max_model_calls,
            max_tool_calls=setup.limits.max_tool_calls,
            max_tokens=setup.limits.max_tokens,
            timeout_seconds=setup.limits.timeout_seconds,
            max_cost_usd=setup.limits.max_cost_usd,
        ),
        month=AiMonthUsage(
            month_start=usage.month_start,
            calls=usage.calls,
            tokens=usage.total_tokens,
            token_limit=setup.monthly_token_limit,
            cost_usd=usage.cost_usd if setup.prices.known else None,
            cost_limit_usd=setup.monthly_cost_limit_usd,
        ),
    )


def _policy_response(
    project: Project, policy: ProjectAiPolicy | None, can_edit: bool
) -> ProjectAiPolicyResponse:
    return ProjectAiPolicyResponse(
        project_id=project.id,
        enabled=bool(policy and policy.enabled),
        max_excerpt_lines=policy.max_excerpt_lines if policy else 120,
        updated_at=policy.updated_at if policy else None,
        version=policy.version if policy else 0,
        can_edit=can_edit,
    )


@router.get(
    "/projects/{project_id}/ai-policy", response_model=ProjectAiPolicyResponse, responses=_ERRORS
)
async def get_ai_policy(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ProjectAiPolicyResponse:
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        policy = await session.get(ProjectAiPolicy, project.id)
        return _policy_response(
            project, policy, principal.has_role(project.workspace_id, MembershipRole.ADMIN)
        )


@router.put(
    "/projects/{project_id}/ai-policy", response_model=ProjectAiPolicyResponse, responses=_ERRORS
)
async def update_ai_policy(
    project_id: uuid.UUID,
    body: ProjectAiPolicyUpdate,
    principal: CurrentPrincipal,
    container: Container,
) -> ProjectAiPolicyResponse:
    """Allow or stop sending masked code excerpts of this project to the AI provider (admins)."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.ADMIN,
        )
        policy = (
            await session.execute(
                select(ProjectAiPolicy)
                .where(ProjectAiPolicy.project_id == project.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if policy is not None and body.version is not None and body.version != policy.version:
            raise ApiError(
                409,
                "version_conflict",
                "The policy changed since you loaded it; reload and try again",
                {"current_version": policy.version},
            )
        before = {"enabled": False, "max_excerpt_lines": 120}
        if policy is None:
            policy = ProjectAiPolicy(
                project_id=project.id,
                workspace_id=project.workspace_id,
                enabled=body.enabled,
                max_excerpt_lines=body.max_excerpt_lines,
                updated_by=principal.user_id,
            )
            session.add(policy)
        else:
            before = {"enabled": policy.enabled, "max_excerpt_lines": policy.max_excerpt_lines}
            policy.enabled = body.enabled
            policy.max_excerpt_lines = body.max_excerpt_lines
            policy.updated_by = principal.user_id
        after = {"enabled": body.enabled, "max_excerpt_lines": body.max_excerpt_lines}
        changes = {key: [before[key], after[key]] for key in after if before[key] != after[key]}
        if changes:
            session.add(
                AiPolicyEvent(
                    project_id=project.id, actor_user_id=principal.user_id, changes=changes
                )
            )
        await session.flush()
        await session.refresh(policy)
        return _policy_response(project, policy, can_edit=True)


# -- runs --------------------------------------------------------------------------------------


_CANDIDATE_FIELDS = set(AiFixCandidateResponse.model_fields)
_FIX_FIELDS = set(AiFixResult.model_fields) - {"candidates"}


def _fix_result(answer: dict[str, object]) -> AiFixResult:
    """The stored fix answer as the API shows it (edits and hashes stay internal)."""
    candidates = answer.get("candidates")
    return AiFixResult.model_validate(
        {
            **{key: value for key, value in answer.items() if key in _FIX_FIELDS},
            "candidates": [
                {key: value for key, value in c.items() if key in _CANDIDATE_FIELDS}
                for c in (candidates if isinstance(candidates, list) else [])
                if isinstance(c, dict)
            ],
        }
    )


def run_response(run: AiRun, findings: list[AiFinding]) -> AiRunResponse:
    return AiRunResponse(
        id=run.id,
        project_id=run.project_id,
        snapshot_id=run.snapshot_id,
        scan_id=run.scan_id,
        finding_id=run.finding_id,
        kind=run.kind,
        state=run.state,
        question=run.question,
        target_paths=run.target_paths,
        provider=run.provider,
        model=run.model,
        prompt_version=run.prompt_version,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        cancel_requested_at=run.cancel_requested_at,
        error_code=run.error_code,
        error_message=run.error_message,
        usage=AiUsageResponse.model_validate(run.usage) if run.usage else None,
        answer=(
            AiAnswerResponse.model_validate(run.answer)
            if run.answer and run.kind != AiRunKind.FIX.value
            else None
        ),
        change_set_id=run.change_set_id,
        fix=_fix_result(run.answer) if run.answer and run.kind == AiRunKind.FIX.value else None,
        steps=[AiStepResponse.model_validate(step) for step in run.steps or []],
        limitations=list(run.limitations or []),
        findings=[
            AiFindingResponse(
                id=f.id,
                title=f.title,
                category=f.category,
                severity=f.severity,
                severity_rationale=f.severity_rationale,
                confidence=f.confidence,
                evidence_class=f.evidence_class,
                anchors=[AiAnchorResponse.model_validate(a) for a in f.anchors],
                triggering_conditions=f.triggering_conditions,
                impact=f.impact,
                recommendation=f.recommendation,
                validation_needed=f.validation_needed,
                uncertainty=f.uncertainty,
                related_finding_id=f.related_finding_id,
            )
            for f in findings
        ],
    )


async def ai_gate(session: AsyncSession, setup: AiSetup, project_id: uuid.UUID) -> ProjectAiPolicy:
    """Refuse an AI run unless the project allows it, the server is set up and budget is left."""
    policy = await session.get(ProjectAiPolicy, project_id)
    if policy is None or not policy.enabled:
        raise ApiError(
            409,
            "ai_policy_disabled",
            "AI review is switched off for this project. A workspace admin can switch it on.",
        )
    if not setup.available or setup.model is None:
        raise ApiError(409, "ai_unavailable", setup.reason or "AI review is not set up")
    usage = await month_usage(session)
    if setup.monthly_token_limit and usage.total_tokens >= setup.monthly_token_limit:
        raise ApiError(429, "ai_monthly_limit", "This month's AI token limit is used up")
    if (
        setup.monthly_cost_limit_usd is not None
        and usage.cost_usd is not None
        and usage.cost_usd >= setup.monthly_cost_limit_usd
    ):
        raise ApiError(429, "ai_monthly_limit", "This month's AI cost limit is used up")
    return policy


def run_limits(setup: AiSetup, policy: ProjectAiPolicy) -> dict[str, object]:
    return {
        "max_model_calls": setup.limits.max_model_calls,
        "max_tool_calls": setup.limits.max_tool_calls,
        "max_tokens": setup.limits.max_tokens,
        "timeout_seconds": setup.limits.timeout_seconds,
        "max_cost_usd": setup.limits.max_cost_usd,
        "max_excerpt_lines": policy.max_excerpt_lines,
    }


async def _target(
    session: AsyncSession, project: Project, body: AiRunCreate
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Resolve (snapshot, scan) for a new run inside the project, or raise a 4xx ApiError."""
    if body.kind == "finding_review":
        if body.finding_id is None:
            raise ApiError(422, "finding_required", "Choose the finding to review")
        finding = await session.get(Finding, body.finding_id)
        if finding is None or finding.project_id != project.id:
            raise ApiError(404, "finding_not_found", "Finding not found in this project")
        return finding.snapshot_id, finding.scan_id
    if body.snapshot_id is not None:
        snapshot = await session.get(Snapshot, body.snapshot_id)
        if snapshot is None or snapshot.project_id != project.id:
            raise ApiError(404, "snapshot_not_found", "Upload not found in this project")
    else:
        snapshot = await session.scalar(
            select(Snapshot)
            .where(
                Snapshot.project_id == project.id,
                Snapshot.capture_status == CaptureStatus.FROZEN.value,
                Snapshot.change_set_id.is_(None),
            )
            .order_by(Snapshot.frozen_at.desc())
            .limit(1)
        )
        if snapshot is None:
            raise ApiError(409, "no_snapshot", "Upload code before asking AI about it")
    if snapshot.capture_status != CaptureStatus.FROZEN.value:
        raise ApiError(409, "snapshot_not_ready", "This upload is not ready yet")
    scan = await session.scalar(
        select(Scan.id)
        .where(Scan.snapshot_id == snapshot.id)
        .order_by(Scan.created_at.desc())
        .limit(1)
    )
    return snapshot.id, scan


@router.post(
    "/projects/{project_id}/ai-runs",
    status_code=202,
    response_model=AiRunResponse,
    responses={
        **_ERRORS,
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def create_ai_run(
    project_id: uuid.UUID, body: AiRunCreate, principal: CurrentPrincipal, container: Container
) -> AiRunResponse:
    """Start a bounded AI run: a question, a review of one finding, or a review of files.

    Refused unless an admin switched AI review on for the project and the server has a
    configured provider with monthly budget left. Only masked excerpts of the chosen upload are
    sent, and every call is accounted.
    """
    if body.kind == "question" and not body.question:
        raise ApiError(422, "question_required", "Type a question")
    if body.kind == "file_review" and not body.paths:
        raise ApiError(422, "paths_required", "Choose one to five files to review")
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
        snapshot_id, scan_id = await _target(session, project, body)
        paths: list[str] | None = None
        if body.kind == "file_review" and body.paths:
            paths = list(dict.fromkeys(body.paths))
            known = set(
                (
                    await session.scalars(
                        select(FileEntry.path).where(
                            FileEntry.snapshot_id == snapshot_id,
                            FileEntry.disposition == FileDisposition.ANALYZABLE.value,
                            FileEntry.path.in_(paths),
                        )
                    )
                ).all()
            )
            missing = [p for p in paths if p not in known]
            if missing:
                raise ApiError(
                    422,
                    "unknown_paths",
                    "Some files are not reviewable files of this upload",
                    {"paths": missing[:5]},
                )
        run = AiRun(
            workspace_id=project.workspace_id,
            project_id=project.id,
            snapshot_id=snapshot_id,
            scan_id=scan_id,
            finding_id=body.finding_id if body.kind == "finding_review" else None,
            kind=body.kind,
            state=AiRunState.QUEUED.value,
            question=body.question if body.kind == "question" else None,
            target_paths=paths,
            provider=setup.provider.value,
            model=setup.model,
            prompt_version=PROMPT_VERSION,
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


@router.get("/projects/{project_id}/ai-runs", response_model=AiRunPage, responses=_ERRORS)
async def list_ai_runs(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    finding_id: Annotated[uuid.UUID | None, Query()] = None,
) -> AiRunPage:
    """Newest runs first; ``finding_id`` narrows to second opinions on that finding."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        query = select(AiRun).where(AiRun.project_id == project.id)
        if finding_id is not None:
            query = query.where(AiRun.finding_id == finding_id)
        runs = (await session.scalars(query.order_by(AiRun.created_at.desc()).limit(limit))).all()
        return AiRunPage(items=[run_response(run, []) for run in runs])


@router.get("/ai-runs/{run_id}", response_model=AiRunResponse, responses=_ERRORS)
async def get_ai_run(
    run_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> AiRunResponse:
    async with transaction(container.session_factory) as session:
        run = await get_scoped(session, principal, AiRun, run_id, not_found="ai_run_not_found")
        findings = (
            await session.scalars(
                select(AiFinding).where(AiFinding.run_id == run.id).order_by(AiFinding.sequence)
            )
        ).all()
        return run_response(run, list(findings))


@router.get(
    "/ai-runs/{run_id}/export",
    responses={**_ERRORS, 409: {"model": ErrorResponse}},
)
async def export_ai_run(
    run_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    format: Annotated[Literal["json", "sarif"], Query()] = "json",
) -> JSONResponse:
    """Download a finished run as JSON (``crp-ai-run-export/v1``) or its AI findings as SARIF.

    Kept separate from scan exports: these are AI results with their evidence class.
    """
    async with transaction(container.session_factory) as session:
        run = await get_scoped(session, principal, AiRun, run_id, not_found="ai_run_not_found")
        if not AiRunState(run.state).is_terminal:
            raise ApiError(409, "ai_run_not_finished", "Exports are available once the run ends")
        findings = (
            await session.scalars(
                select(AiFinding).where(AiFinding.run_id == run.id).order_by(AiFinding.sequence)
            )
        ).all()
        project = await session.get(Project, run.project_id)
        snapshot = await session.get(Snapshot, run.snapshot_id)
        response = run_response(run, list(findings))
    export = build_ai_export(
        response.model_dump(mode="json"),
        project={"id": str(run.project_id), "name": project.name if project else ""},
        snapshot={
            "id": str(run.snapshot_id),
            "manifest_sha256": snapshot.manifest_sha256 if snapshot else None,
        },
        tool_version=__version__,
        generated_at=datetime.now(UTC).isoformat(),
    )
    document = build_ai_sarif(export) if format == "sarif" else export
    filename = f"refactorx-ai-{run.id.hex[:12]}.{'sarif' if format == 'sarif' else 'json'}"
    return JSONResponse(
        document,
        media_type="application/sarif+json" if format == "sarif" else "application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/ai-runs/{run_id}/cancel", response_model=AiRunResponse, responses=_ERRORS)
async def cancel_ai_run(
    run_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> AiRunResponse:
    async with transaction(container.session_factory) as session:
        run = await get_scoped(
            session,
            principal,
            AiRun,
            run_id,
            not_found="ai_run_not_found",
            required=MembershipRole.MEMBER,
            for_update=True,
        )
        if not AiRunState(run.state).is_terminal and run.cancel_requested_at is None:
            run.cancel_requested_at = datetime.now(UTC)
        await session.flush()
        await session.refresh(run)
        response = run_response(run, [])
        terminal = AiRunState(run.state).is_terminal
    if not terminal:
        try:
            await container.workflows.cancel_ai_run(run_id)
        except WorkflowUnavailableError as exc:
            raise ApiError(503, "workflow_unavailable", str(exc)) from exc
    return response
