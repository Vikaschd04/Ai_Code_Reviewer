# Validation — refactorX rename, demo account, sample project and reviewer-first UI (ADR 0011)

Date: 30 September 2026. Scope: owner request (not a phase gate).

## Environment

macOS 26.5.2 arm64 (Apple M2, 8 GB); Python 3.14.3; Node 22; pnpm 11.20.0; PostgreSQL 18.6; Temporal CLI 1.9.1; PMD 7.27.0; ESLint 10.11.0; Opengrep 1.30.0; Trivy 0.69.3 (offline DB); Google Chrome for Playwright.

## Commands and outcomes

| Command | Outcome |
|---|---|
| `make contracts` | OpenAPI + TypeScript contract regenerated (new `AuthOptions`, `SampleProjectCreate/Response`, `is_demo`) |
| `make check` | exit 0 (ruff, mypy strict, contract drift, web typecheck/lint/format) |
| `make test` | exit 0: 311 pytest passed (0 skipped; real PostgreSQL, Temporal, engines) + 14 vitest |
| `make test-e2e` | 12/12 passed on a fresh isolated stack with the production web build |

New and changed evidence:

- `services/api/tests/test_demo_and_sample.py` (7): deterministic sample archive with a generated fake token; demo cookies rejected once the demo is disabled; public sign-in options; demo disabled → 404; missing Origin → 403; demo user sees only the `demo` workspace (member), cannot read the owner's project (404) or create in the owner's workspace (404), cannot run diagnostics (403); project quota → 429 (owner unaffected); sample created through the intake path (VALIDATING, workflow started, archive size recorded; a second sample gets "(2)"); unavailable workflow service → 503 with project and intake ids.
- `services/worker/tests/test_sample_project.py`: a demo user creates the sample, the worker freezes it, the hourly scan quota (1) rejects a second scan with 429, and the real engines report all planted problems — PMD (UseEqualsToCompareStrings, EmptyCatchBlock, CloseResource, HardCodedCryptoKey), ESLint (no-eval, no-dupe-keys, use-isnan, eqeqeq), Opengrep (SQL injection, Runtime.exec, weak hash, ECB, innerHTML), Trivy (CVE-2021-44228 log4j-core 2.14.1, CVE-2021-23337 lodash 4.17.15, github-pat secret).
- `tools/devtools/tests/test_hosted_smoke.py`: the CI smoke script now also signs in as the demo user (Secure cookie in hosted mode) and reviews the sample project — passes through the real `crp-dev hosted` entrypoint in the standard and lite profiles.
- `apps/web/e2e/ui-tour.spec.ts`: demo sign-in, one-click sample review with real engines, every main screen, technical details collapsed by default, no horizontal page scroll at 390 px; screenshots `tour-*.png` (light, dark, mobile). `foundation`, `p01` and `p02` specs updated to the new wording (technical assertions now open "Technical details").

## Visual review (screenshots)

Reviewed: sign-in (desktop, mobile dark), overview (empty, populated, dark, mobile), review results (light, dark, mobile), finding, project, issues, architecture, upload, projects, system status. Issues found and fixed during review: progress list indented by default list padding; file paths truncated before the file name (now name first, folder muted); issue paths breaking mid-word; a single status tile stretching full width; finding guidance placed below the triage form (now under the code); four stat tiles stacked one per row on mobile (now two columns); legacy "Local · loopback only" badge and loopback-only footer shown in hosted mode; developer-only runner command and token hints shown in hosted mode. Dark-mode navigation text looked faint only because the screenshot was taken during the 150 ms colour transition; the tour now waits for it.

## CI (commit 806df8d)

`quality` and `container` succeeded. Container smoke tests (Linux image, Docker Compose with PostgreSQL 18), from the public check-run annotations:

- Standard profile: smoke scan SUCCEEDED (all six engines); demo sign-in + sample review SUCCEEDED, 41 findings.
- Lite profile under 512 MiB / no swap / 0.1 CPU (Render free): smoke scan SUCCEEDED (all six engines, slowest API response 0.9 s) and demo sample review SUCCEEDED, 41 findings; `memory.peak` 256,577,536 bytes (≈ 245 MiB); restarts 0; OOM kills 0.

## Remaining gaps

- The demo workspace is shared by all demo visitors and has no automatic cleanup (K-P10-01, K-P10-02).
- The live Render deployment has not been created yet (owner action).
