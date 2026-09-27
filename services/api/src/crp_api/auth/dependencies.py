"""FastAPI dependencies that authenticate requests and enforce cookie-origin (CSRF) checks.

Two levels exist: ``AuthenticatedRequest`` verifies credentials only (used by readiness, which
must work while the database is down), and ``CurrentPrincipal`` additionally resolves the
platform user and workspace grants from PostgreSQL (used by every data endpoint).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.exc import SQLAlchemyError

from crp_api.auth.principal import (
    Principal,
    PrincipalDisabledError,
    PrincipalNotProvisionedError,
    load_principal,
)
from crp_api.auth.provider import AuthenticatedSubject, AuthMethod
from crp_api.container import AppContainer, get_container
from crp_api.errors import ApiError
from crp_core.db.session import transaction

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
Container = Annotated[AppContainer, Depends(get_container)]


def require_allowed_origin(request: Request, container: AppContainer) -> None:
    """Cookie-authenticated state changes must come from a configured web origin."""
    origin = request.headers.get("origin")
    if origin is None or origin not in container.settings.allowed_web_origins:
        raise ApiError(403, "origin_rejected", "Request origin is not allowed for this action")


async def get_authenticated_subject(request: Request, container: Container) -> AuthenticatedSubject:
    authenticated = await container.identity.authenticate(request)
    if authenticated is None:
        raise ApiError(
            401,
            "authentication_required",
            "Valid credentials are required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if authenticated.method is AuthMethod.SESSION_COOKIE and request.method not in SAFE_METHODS:
        require_allowed_origin(request, container)
    return authenticated


AuthenticatedRequest = Annotated[AuthenticatedSubject, Depends(get_authenticated_subject)]


async def get_principal(subject: AuthenticatedRequest, container: Container) -> Principal:
    try:
        async with transaction(container.session_factory) as session:
            return await load_principal(session, subject.subject, subject.method)
    except PrincipalNotProvisionedError as exc:
        raise ApiError(
            403,
            "identity_not_provisioned",
            "The authenticated identity has no platform user; run the migrate command",
        ) from exc
    except PrincipalDisabledError as exc:
        raise ApiError(403, "identity_disabled", "This identity has been disabled") from exc
    except (SQLAlchemyError, OSError) as exc:
        raise ApiError(
            503,
            "database_unavailable",
            "The database is unavailable or not migrated; check /v1/health/ready",
        ) from exc


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def require_operator(principal: CurrentPrincipal) -> Principal:
    if not principal.is_operator:
        raise ApiError(403, "operator_role_required", "An admin or owner role is required")
    return principal


OperatorPrincipal = Annotated[Principal, Depends(require_operator)]
