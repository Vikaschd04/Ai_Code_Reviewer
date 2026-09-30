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
| ADR 0009 single-user hosted deployment | Accepted, implemented; amended to Render-only (UI served by the container) | 2026-09-27 | Hosted tier with Host allowlist, secure cookies, upload tickets; one Render Blueprint (web service + PostgreSQL), CI-gated deploys |
| ADR 0010 lite profile for free hosting | Accepted, implemented | 2026-09-30 | One process without Temporal (in-process runner with resume), artifacts in PostgreSQL, Trivy DB baked in; fits Render free (512 MB, 0.1 CPU) |
| ADR 0011 refactorX, demo and reviewer-first UI | Accepted, implemented | 2026-09-30 | User-facing rename; shared demo workspace with quotas; one-click sample; plain language with technical details collapsed |
| ADR 0012 bounded AI review | Accepted, implemented offline (live gate blocked on key) | 2026-09-30 | Anthropic or OpenAI-compatible, per-project opt-in off by default, read-only snapshot tools, citations checked against the upload, spend caps |
| ADR 0013 framework packs | Accepted, implemented (experimental) | 2026-09-30 | SAP Commerce and Salesforce packs as adapters with capability records; secure XML; rules on pinned PMD/Opengrep plus a small in-process engine |
| ADR 0014 validated fixes | Accepted, deterministic slice implemented | 2026-10-01 | Recipes first; proposals bound to upload/base/patch/result hashes; policy refuses suppressions and weakened tests; source-level ladder on copies, tests/build "not run" until an isolated runner exists |

Add implementation decisions only after recording alternatives, consequences and evidence in an ADR. Do not convert speculative preferences into verified implementation facts.
