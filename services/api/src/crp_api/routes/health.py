from __future__ import annotations

from fastapi import APIRouter, Response

from crp_api import __version__
from crp_api.auth.dependencies import AuthenticatedRequest, Container
from crp_api.errors import ErrorResponse
from crp_api.schemas import CapabilityList, LivenessResponse, OverallReadiness, ReadinessReport
from crp_api.services.capabilities import capabilities_for
from crp_api.services.readiness import build_readiness_report

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Process liveness only; reveals no dependency details and needs no credentials."""
    return LivenessResponse(status="alive", service="crp-api", version=__version__)


@router.get(
    "/health/ready",
    response_model=ReadinessReport,
    responses={503: {"model": ReadinessReport}, 401: {"model": ErrorResponse}},
)
async def readiness(
    container: Container, subject: AuthenticatedRequest, response: Response
) -> ReadinessReport:
    """Check real dependencies; 503 with the same body when any check is not OK.

    Only credentials are verified (no database lookup) so the report works during outages.
    """
    report = await build_readiness_report(container)
    if report.status is not OverallReadiness.READY:
        response.status_code = 503
    return report


@router.get(
    "/capabilities", response_model=CapabilityList, responses={401: {"model": ErrorResponse}}
)
async def capabilities(subject: AuthenticatedRequest, container: Container) -> CapabilityList:
    return CapabilityList(capabilities=capabilities_for(container.settings))
