"""Local-token identity provider for the single-user local and hosted modes.

The generated token (outside source control) authenticates API clients via
``Authorization: Bearer``. Browsers exchange it once for a signed, HttpOnly, SameSite=Strict
session cookie so the token itself is not kept in page storage. Rotating the token file
invalidates every issued session because the signing key is derived from it.

When the demo account is enabled, a demo session (no credentials) is a cookie for the demo
subject; it is accepted only while the demo stays enabled and never via the bearer token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Request

from crp_api.auth.provider import AuthenticatedSubject, AuthMethod
from crp_core.db.identity import DEMO_SUBJECT, LOCAL_SUBJECT

SESSION_COOKIE = "crp_session"
_SESSION_VERSION = "v1"
_TICKET_VERSION = "t1"
UPLOAD_TICKET_TTL_SECONDS = 15 * 60


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True, slots=True)
class IssuedSession:
    cookie_value: str
    expires_at: datetime
    max_age_seconds: int


class LocalTokenProvider:
    def __init__(self, token: str, session_ttl_seconds: int, *, demo_enabled: bool = False) -> None:
        self._token = token.encode("utf-8")
        self._subjects = frozenset(
            {LOCAL_SUBJECT, DEMO_SUBJECT} if demo_enabled else {LOCAL_SUBJECT}
        )
        self._signing_key = hmac.new(
            self._token, b"crp-session-signing-v1", hashlib.sha256
        ).digest()
        self._ttl = session_ttl_seconds
        self._ticket_key = hmac.new(self._token, b"crp-upload-ticket-v1", hashlib.sha256).digest()

    @property
    def token_length(self) -> int:
        return len(self._token)

    def issue_upload_ticket(self, intake_id: str, now: float | None = None) -> tuple[str, datetime]:
        """Short-lived credential that authorizes one thing: uploading one intake's archive."""
        expires = int(time.time() if now is None else now) + UPLOAD_TICKET_TTL_SECONDS
        payload = _b64e(
            json.dumps(
                {"iid": intake_id, "exp": expires, "n": secrets.token_hex(8)},
                separators=(",", ":"),
            ).encode()
        )
        signed = f"{_TICKET_VERSION}.{payload}"
        signature = _b64e(hmac.new(self._ticket_key, signed.encode(), hashlib.sha256).digest())
        return f"{signed}.{signature}", datetime.fromtimestamp(expires, UTC)

    def verify_upload_ticket(self, ticket: str, intake_id: str, now: float | None = None) -> bool:
        parts = ticket.split(".")
        if len(parts) != 3 or parts[0] != _TICKET_VERSION:
            return False
        signed = f"{parts[0]}.{parts[1]}"
        expected = _b64e(hmac.new(self._ticket_key, signed.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, parts[2]):
            return False
        try:
            claims = json.loads(_b64d(parts[1]))
        except ValueError:
            return False
        current = time.time() if now is None else now
        return (
            isinstance(claims, dict)
            and claims.get("iid") == intake_id
            and isinstance(claims.get("exp"), int)
            and claims["exp"] > current
        )

    def token_matches(self, presented: str) -> bool:
        return hmac.compare_digest(presented.encode("utf-8"), self._token)

    def issue_session(
        self, now: float | None = None, *, subject: str = LOCAL_SUBJECT
    ) -> IssuedSession:
        if subject not in self._subjects:
            raise ValueError("sessions can only be issued for known, enabled subjects")
        issued = int(time.time() if now is None else now)
        expires = issued + self._ttl
        payload = _b64e(
            json.dumps(
                {"sub": subject, "iat": issued, "exp": expires, "sid": secrets.token_hex(8)},
                separators=(",", ":"),
            ).encode()
        )
        signed = f"{_SESSION_VERSION}.{payload}"
        signature = _b64e(hmac.new(self._signing_key, signed.encode(), hashlib.sha256).digest())
        return IssuedSession(
            cookie_value=f"{signed}.{signature}",
            expires_at=datetime.fromtimestamp(expires, UTC),
            max_age_seconds=self._ttl,
        )

    def verify_session(self, value: str, now: float | None = None) -> str | None:
        """Return the session subject if the cookie is authentic and unexpired."""
        parts = value.split(".")
        if len(parts) != 3 or parts[0] != _SESSION_VERSION:
            return None
        signed = f"{parts[0]}.{parts[1]}"
        expected = _b64e(hmac.new(self._signing_key, signed.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, parts[2]):
            return None
        try:
            claims = json.loads(_b64d(parts[1]))
        except ValueError:
            return None
        current = time.time() if now is None else now
        if not isinstance(claims, dict) or not isinstance(claims.get("exp"), int):
            return None
        subject = claims.get("sub")
        if claims["exp"] <= current or subject not in self._subjects:
            return None
        return str(subject)

    async def authenticate(self, request: Request) -> AuthenticatedSubject | None:
        authorization = request.headers.get("authorization")
        if authorization is not None:
            scheme, _, credential = authorization.partition(" ")
            if scheme.lower() == "bearer" and credential and self.token_matches(credential.strip()):
                return AuthenticatedSubject(subject=LOCAL_SUBJECT, method=AuthMethod.BEARER_TOKEN)
            return None
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie and (subject := self.verify_session(cookie)) is not None:
            return AuthenticatedSubject(subject=subject, method=AuthMethod.SESSION_COOKIE)
        return None
