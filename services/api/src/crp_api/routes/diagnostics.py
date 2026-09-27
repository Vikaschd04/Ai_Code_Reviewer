"""Operator diagnostics. The diagnostic workflow proves API → Temporal → worker → storage/DB
execution; it performs no code analysis and produces no findings."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from crp_api.auth.dependencies import Container, OperatorPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import DiagnosticRunResponse
from crp_core.workflows.contracts import DiagnosticWorkflowInput
from crp_core.workflows.gateway import (
    WorkflowRunNotFoundError,
    WorkflowRunStatus,
    WorkflowUnavailableError,
)

router = APIRouter(
    prefix="/diagnostics", tags=["diagnostics"], responses={401: {"model": ErrorResponse}}
)

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


def _unavailable(exc: WorkflowUnavailableError) -> ApiError:
    return ApiError(503, "workflow_unavailable", str(exc))


@router.post(
    "/workflow-runs", status_code=202, response_model=DiagnosticRunResponse, responses=_ERRORS
)
async def start_workflow_diagnostic(
    principal: OperatorPrincipal, container: Container
) -> DiagnosticRunResponse:
    payload = DiagnosticWorkflowInput(run_id=uuid.uuid4(), requested_by=principal.user_id)
    try:
        workflow_id = await container.workflows.start_diagnostic(payload)
    except WorkflowUnavailableError as exc:
        raise _unavailable(exc) from exc
    return DiagnosticRunResponse(workflow_id=workflow_id, status=WorkflowRunStatus.RUNNING)


@router.get(
    "/workflow-runs/{workflow_id}",
    response_model=DiagnosticRunResponse,
    responses={**_ERRORS, 404: {"model": ErrorResponse}},
)
async def get_workflow_diagnostic(
    workflow_id: str, principal: OperatorPrincipal, container: Container
) -> DiagnosticRunResponse:
    try:
        run = await container.workflows.get_diagnostic(workflow_id)
    except WorkflowRunNotFoundError as exc:
        raise ApiError(404, "workflow_run_not_found", "Diagnostic run not found") from exc
    except WorkflowUnavailableError as exc:
        raise _unavailable(exc) from exc
    return DiagnosticRunResponse(
        workflow_id=run.workflow_id,
        status=run.status,
        started_at=run.started_at,
        closed_at=run.closed_at,
        result=run.result,
        failure_message=run.failure_message,
    )
