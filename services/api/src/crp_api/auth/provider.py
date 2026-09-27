"""Identity-provider boundary.

An identity provider turns request credentials into an authenticated external *subject*. The
platform then maps that subject to a user and workspace grants stored in PostgreSQL
(``crp_api.auth.principal``). Local-token mode is the only implemented provider; a reviewed
OIDC/SAML provider for hosted deployments (P07) implements the same protocol without changing
authorization code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from fastapi import Request


class AuthMethod(StrEnum):
    BEARER_TOKEN = "bearer_token"  # noqa: S105 - enum label, not a credential
    SESSION_COOKIE = "session_cookie"


@dataclass(frozen=True, slots=True)
class AuthenticatedSubject:
    subject: str
    method: AuthMethod


class IdentityProvider(Protocol):
    async def authenticate(self, request: Request) -> AuthenticatedSubject | None:
        """Return the authenticated subject, or None when credentials are absent or invalid."""
        ...
