# ADR 0006 — Baseline engines, trusted configuration and scan execution

Status: accepted and implemented, 26 September 2026 (P01). Owner: development agent for the repository maintainer.

Context and constraints: P01 requires real PMD and ESLint results with coverage, bounded execution, cancellation and honest failure states. Uploaded repositories are untrusted: their configuration, inline directives and instruction files must not change platform rules.

Decision:

- **PMD 7.27.0** (official `pmd-dist` release, SHA-256 pinned in `crp_devtools.engines`, installed by `make engines` into `.local/engines`). 7.28.0 was one day old at adoption time and was not taken. Java only in P01 (Apex belongs to the P04 Salesforce pack). Ruleset `crp-pmd-java-v1` (35 built-in rules, deprecated-for-removal rules excluded) is owned by the platform.
- **ESLint 10.11.0** in an isolated pnpm package `engines/eslint-runner` (separate from the web app's lint setup) with typescript-eslint 8.70.1. `run.mjs` lints exactly the listed files with `overrideConfigFile` = platform config `crp-eslint-v1`, `allowInlineConfig: false`, no globbing, no project config lookup.
- **Source cannot suppress platform rules:** ESLint inline directives are disabled; PMD runs with a random per-run `--suppress-marker` (so `// NOPMD` has no effect) and `--show-suppressed`; annotation-suppressed violations are still reported and labelled "suppressed in source".
- **Tree-sitter 0.26** (Java 0.23.5, JavaScript 0.25.0, TypeScript 0.23.2 grammars) for in-process syntax structure (symbols, parse status). It is labelled syntax-only: no type or cross-file resolution.
- **Execution:** each engine runs in a Temporal activity over a fresh, read-only materialized copy of the snapshot's eligible files under `CRP_WORK_ROOT` (outside the repo), via a fixed executable + argument list, own process group, scrubbed environment (no `CRP_*` secrets), wall-clock timeout, output cap, JVM/Node heap caps; cancellation kills the process group. Heartbeat throttling is capped at 2 s so cancel takes effect within seconds.
- **Outcomes:** UNAVAILABLE (missing tool), NOT_APPLICABLE (no eligible files), FAILED (crash/timeout/malformed output — all files NOT_ATTEMPTED), PARTIAL (some files failed), SUCCEEDED. A scan is PARTIAL when any applicable engine is not fully successful and FAILED only when none completed. Per-file coverage rows record ANALYZED / FAILED / NOT_ATTEMPTED with reasons.
- **Normalization:** fingerprint = sha256(`crp-fp-v1`, engine, rule, path, whitespace-normalized source text at the span start, occurrence index) — stable when unrelated lines move, distinct for repeated identical constructs. Canonical severity/category and static guidance come from `rules/catalog.json` (64 entries); uncatalogued rules are reported with an explicit `in_catalog=false` fallback, never dropped.
- Raw engine reports are stored (work-dir paths redacted) as artifacts and referenced by SHA-256 from the engine run.

Alternatives considered: running each project's own ESLint config (executes project code; rejected until a sandboxed opt-in mode exists); Semgrep/Sonar/CodeQL rules (licensing constraints, see ENGINE_ADOPTION.md); container-per-engine isolation (not available on this host; required before multi-tenant hosting).

Consequences: results are reproducible for a given snapshot + engine + ruleset hash. There is no OS-level CPU/memory/network sandbox on local macOS: limits are wall time, output size and heap flags. PMD's upstream rule behavior is authoritative (for example, it ignores unused locals named `unused`/`ignored`), and fixtures were adjusted to real behavior rather than the reverse.

Evidence: `packages/analysis/tests/test_engines.py` (real PMD/ESLint seeded/clean/crash/timeout/cancel/malformed/unavailable), `services/worker/tests/test_p01_pipeline.py`, docs/validation/P01_REPORT.md.
