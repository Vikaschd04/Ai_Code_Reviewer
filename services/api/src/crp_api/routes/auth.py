from __future__ import annotations

from fastapi import APIRouter, Request, Response

from crp_api.auth.dependencies import Container, CurrentPrincipal, require_allowed_origin
from crp_api.auth.local_token import SESSION_COOKIE
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    PrincipalResponse,
    SessionRequest,
    SessionResponse,
    WorkspaceGrantResponse,
)
from crp_core.db.identity import LOCAL_SUBJECT

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/session",
    response_model=SessionResponse,
    responses={401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
async def create_session(
    body: SessionRequest, request: Request, response: Response, container: Container
) -> SessionResponse:
    """Exchange the local development token for an HttpOnly session cookie."""
    require_allowed_origin(request, container)
    if (retry_after := container.login_throttle.retry_after()) is not None:
        raise ApiError(
            429,
            "too_many_attempts",
            "Too many failed sign-in attempts; try again later",
            headers={"Retry-After": str(retry_after)},
        )
    if not container.identity.token_matches(body.token):
        container.login_throttle.record_failure()
        raise ApiError(401, "invalid_credentials", "The token is not valid")
    session = container.identity.issue_session()
    response.set_cookie(
        SESSION_COOKIE,
        session.cookie_value,
        max_age=session.max_age_seconds,
        httponly=True,
        samesite="strict",
        # Loopback development uses plain HTTP; hosted mode is always behind TLS.
        secure=container.settings.hosted,
        path="/",
    )
    return SessionResponse(subject=LOCAL_SUBJECT, expires_at=session.expires_at)


@router.delete("/session", status_code=204)
async def delete_session(response: Response, container: Container) -> Response:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="strict",
        secure=container.settings.hosted,
    )
    response.status_code = 204
    return response


@router.get("/me", response_model=PrincipalResponse, responses={401: {"model": ErrorResponse}})
async def current_principal(principal: CurrentPrincipal) -> PrincipalResponse:
    return PrincipalResponse(
        user_id=principal.user_id,
        subject=principal.subject,
        display_name=principal.display_name,
        auth_method=principal.auth_method.value,
        is_operator=principal.is_operator,
        workspaces=[
            WorkspaceGrantResponse(
                workspace_id=grant.workspace_id, slug=grant.slug, name=grant.name, role=grant.role
            )
            for grant in principal.grants
        ],
    )
