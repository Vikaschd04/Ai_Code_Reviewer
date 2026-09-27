# Decision index

| Decision | Status | Date | Reason |
|---|---|---|---|
| ADR 0001 upload-first intake | Accepted specification | 2026-09-26 | User requested source upload/folder intake before Git |
| ADR 0002 replaceable isolated engines | Accepted specification | 2026-09-26 | Reuse analyzers while owning product contracts and safety |
| ADR 0003 workspace layout and contract generation | Accepted, implemented (P00) | 2026-09-26 | One uv + one pnpm workspace; Pydantic → OpenAPI → TS types with drift checks |
| ADR 0004 local-token auth and IdP boundary | Accepted, implemented (P00) | 2026-09-26 | Loopback-only generated token, HttpOnly sessions, subject → principal mapping |
| ADR 0005 native local services and real test infra | Accepted, implemented (P00) | 2026-09-26 | No container runtime available; real ephemeral PG/Temporal instead of mocks |
| ADR 0006 baseline engines and trusted execution | Accepted, implemented (P01) | 2026-09-26 | PMD 7.27.0 + isolated ESLint 10.11.0 with platform-owned rules; source cannot suppress rules; bounded copies |
| ADR 0007 Opengrep and Trivy adoption | Accepted, implemented (P02) | 2026-09-27 | Owned Opengrep rules; Trivy 0.69.3 (verified-safe after the March 2026 compromise), SHA-256 + Sigstore, fully offline, repo config inert |
| ADR 0008 snapshot graph, issue lifecycle and cache | Accepted, implemented (P02) | 2026-09-27 | Superseding graph builds (no stale links), strict recheck states, correlation keys, content/rule/config-keyed per-file cache |

Add implementation decisions only after recording alternatives, consequences and evidence in an ADR. Do not convert speculative preferences into verified implementation facts.
