# ADR 0004 — Loopback local-token authentication and the identity-provider boundary

Status: accepted and implemented, 26 September 2026 (P00). Owner: development agent for the repository maintainer.

Context and constraints: P00 requires authenticated loopback-only development access with a generated secret outside source control, refusal of insecure non-local startup, and a defined boundary for later identity providers. No hosted deployment or IdP exists yet.

Decision:

- `make bootstrap` generates a 256-bit URL-safe token in `.local/secrets/local-api-token` (mode 0600, directory 0700, git-ignored). Secret files that are symlinks or group/world-readable are refused.
- API clients send `Authorization: Bearer <token>`. Browsers POST the token once to `/v1/auth/session` and receive an **HttpOnly, SameSite=Strict** session cookie (HMAC-SHA256, key derived from the token, default 12 h TTL). The token is never stored in browser storage. Rotating the token file invalidates all sessions.
- CSRF: cookie-authenticated non-GET requests (and the sign-in call) must carry an `Origin` in `CRP_ALLOWED_WEB_ORIGINS`. The web UI is served same-origin via the Vite proxy; no CORS is enabled.
- Loopback enforcement is layered: settings validation refuses non-loopback `CRP_API_HOST` and non-loopback web origins (API, worker and tools exit with code 2); middleware rejects non-loopback peers and non-loopback `Host` headers (DNS-rebinding defence); failed sign-ins are throttled (10 per 60 s → 429).
- Identity boundary: `IdentityProvider.authenticate(request) → AuthenticatedSubject` (`crp_api.auth.provider`). The platform maps the subject to a PostgreSQL user and workspace grants (`crp_api.auth.principal`). Local mode provisions one subject `local:developer` as owner of workspace `local` (`crp-dev migrate`). A reviewed OIDC/SAML provider (P07) implements the same protocol; authorization code does not change.
- Readiness authenticates credentials only (no DB lookup) so it can report a down database; data endpoints resolve the full principal and return `503 database_unavailable` if the DB is down.

Alternatives considered: unauthenticated loopback (rejected: other local processes/browsers could call the API); bearer token kept in `sessionStorage` (rejected: XSS-exposed); server-side session table (unnecessary for single-user local mode); enabling CORS (unnecessary with a same-origin proxy).

Consequences: sessions cannot be revoked individually (rotate the token). `Secure` cookies are off because local mode is plain HTTP on loopback; any hosted mode must add TLS, `Secure` cookies, a reviewed IdP and new configuration tiers. `CRP_ENVIRONMENT=hosted` is rejected by validation today.

Evidence: services/api/tests/test_health_and_auth.py (401/403/429 paths, cookie flags, origin checks, tampered/expired sessions, non-loopback peer and Host refusal), packages/core/tests/test_config.py (bind/origin refusal), live refusal of `CRP_API_HOST=0.0.0.0` recorded in docs/validation/P00_REPORT.md.

Affected contracts/phases/tests: `/v1/auth/*`, all authenticated endpoints; P07 identity hardening.
