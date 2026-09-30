"""AI review (P03; ADR 0012): server status, per-project source-disclosure policy, runs."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from crp_analysis.ai.config import resolve
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    AiLimits,
    AiMonthUsage,
    AiStatus,
    ProjectAiPolicyResponse,
    ProjectAiPolicyUpdate,
)
from crp_api.services.scope import get_scoped
from crp_core.db.ai_usage import month_usage
from crp_core.db.models import AiPolicyEvent, Project, ProjectAiPolicy
from crp_core.db.session import transaction
from crp_core.domain.states import MembershipRole

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
