# Phase status

Last updated: 30 September 2026 (P00–P02 COMPLETE; P03 BLOCKED on the live-provider gate, offline scope done).

| Phase | Status | Evidence | Next task |
|---|---|---|---|
| P00 Foundation | COMPLETE | [P00_REPORT](validation/P00_REPORT.md): `make check` exit 0; 124 pytest + 5 vitest pass; `make test-e2e` 5/5; live `make dev` + `make doctor` 0 failing | — |
| P01 Source/baseline | COMPLETE | [P01_REPORT](validation/P01_REPORT.md): `make check` exit 0; 203 pytest + 12 vitest; `make test-e2e` 8/8; ZIP ≡ folder manifests/findings; real PMD 7.27.0 / ESLint 10.11.0 | — |
| P02 Graph/analyzers | COMPLETE | [P02_REPORT](validation/P02_REPORT.md): `make check` exit 0; 265 pytest + 14 vitest; `make test-e2e` 10/10; real Opengrep 1.30.0 / Trivy 0.69.3 (offline DB); schema 0003; benchmark recorded | — |
| P03 Agentic analysis | BLOCKED | [P03_REPORT](validation/P03_REPORT.md): adapter (Anthropic + OpenAI-compatible), per-project opt-in, bounded planner/investigator/verifier, runs API, Temporal + lite workflows, AI UI, labelled eval set + harness, [OCR evaluation](validation/P03_OCR_EVALUATION.md) (not adopted); `make check` exit 0, `make test`, `make test-e2e` 14/14. All offline mandatory checks pass. **Blocked:** live-provider evaluation (precision/recall, tokens, cost) needs the owner's API key (K-P03-01) | Owner configures `CRP_AI_*`; then `crp-dev ai-eval --split all --live` and record results |
| P04 Frameworks | NOT_STARTED | None | After required foundations |
| P05 Validated fixes | NOT_STARTED | None | After P03/P04 |
| P06 Git/incremental | NOT_STARTED | None | After applicable earlier gates |
| P07 Production | NOT_STARTED | None | After launch scope gates |

Allowed: NOT_STARTED, IN_PROGRESS, BLOCKED, COMPLETE. Link validation reports and list blocked capabilities separately. Passing documentation-link checks does not complete P00 or any software phase.

Scope limits (not blockers): phase gates verified on macOS arm64; the Linux container image is built and smoke-tested in CI (standard profile, and lite profile under 512 MB / 0.1 CPU), but the live hosted deployment has not been created yet and Windows is untested; optional P01 intake modes (browser folder, registered mounts) not implemented. See docs/memory/KNOWN_ISSUES.md.
