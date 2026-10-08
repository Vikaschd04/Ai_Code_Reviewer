# refactorX

Evidence-based code review for Java, JavaScript/TypeScript, SAP Commerce and Salesforce: upload your code, get security flaws, bugs and vulnerable dependencies with the exact location and how to fix them, and download checked fixes for selected findings.

(Internal package and setting names still use `crp` from the project's working name, "Code Review Platform"; see ADR 0011.)

**Current state: Phases 0–2 and 4 passed their mandatory gates on macOS. Phase 3 (AI review) waits only for a live provider key. Phase 5 (fixes) delivers deterministic fixes with source-level checks. Phase 6 (GitHub) is implemented and verified against a test double; its live check waits for your GitHub App. Phase 8 (fix workspaces) delivers manual and bulk fixing, re-checks, comparison and downloads; AI candidates come next. See [phase status](docs/PHASE_STATUS.md).** You can:

- upload a ZIP or capture a local folder, and review the frozen scope;
- run real PMD, ESLint, Opengrep and Trivy scans (plus SAP Commerce and Salesforce checks);
- triage durable issues, compare scans and export JSON/SARIF;
- explore an evidence-backed architecture graph;
- ask an AI about the code when an operator sets up a provider and a project admin switches it on;
- prepare, check and download fixes for selected findings;
- fix many issues at once in a workspace (by hand in a code editor or automatically), re-check them, compare versions and download a patch, only the changed files or the full project;
- connect GitHub repositories so pushes and pull requests are reviewed automatically.

Analysis and fix checks are source-level: nothing is compiled, built, tested or deployed.

## What works today

- **Try it in one click:** the sign-in page offers **Try the demo** (a shared demo workspace, no token) and every workspace offers **Try the sample project** — a small, deliberately flawed Java/TypeScript online store that is uploaded and reviewed by every analyzer with one click.
- **Reviewer-first UI:** plain-language screens (reviews, uploads, findings, issues, changes, architecture); versions, hashes and other provenance sit behind "Technical details"; administration pages only for administrators; light, dark and mobile layouts.
- **Source intake:** ZIP upload (streamed, bounded, attack-resistant) and `crp-runner capture` for a local folder (secrets and dependency/build output skipped on your machine); both produce the same content-addressed snapshot.
- **Scope review:** every entry accounted for (analyzable, excluded with reason, binary, oversized), languages, build/framework indicators with version confidence.
- **Baseline analysis:** Tree-sitter structure, PMD 7.27.0 and ESLint 10.11.0 with platform-owned rules that uploaded code cannot disable; per-file coverage; failures and partial results shown honestly; live progress and cancellation.
- **Findings:** severity/category, exact span (or dependency anchor without an invented line), fingerprint, engine/rule versions, static or advisory guidance, masked source excerpt, cross-engine "also reported by" correlation.
- **Security engines:** Opengrep 1.30.0 with platform-owned rules and Trivy 0.69.3 (known-vulnerable dependencies and exposed secrets), both pinned and signature-verified; Trivy runs fully offline and secret values are never stored.
- **Issues:** durable issues across scans with triage (owner, accepted risk with expiry, false positive) and strict recheck states — an issue is resolved only when a compatible scan verifies its absence.
- **Comparison and exports:** new / still present / verified absent / not rechecked / unknown / rule obsolete between any two scans; schema-validated JSON and SARIF 2.1.0 downloads.
- **Architecture:** snapshot graph of modules, files, types and relations with source evidence and resolved/declared/inferred/unresolved classification; bounded neighborhood and impact views with table equivalents.
- **Caching:** per-file results reused only for identical content, engine, rules and configuration; full rescans on demand.
- **AI review (optional):** Anthropic or any OpenAI-compatible provider, configured by the operator. It is off for every project until a workspace admin switches it on. Questions, file reviews and second opinions cite lines that are checked against the upload. Runs are bounded by per-run and monthly limits ([ADR 0012](docs/adr/0012_BOUNDED_AI_REVIEW.md)).
- **SAP Commerce and Salesforce (experimental):** version detection, platform-support coverage, configuration-driven architecture links, SAP/Java Opengrep rules, PMD Apex rules, and extension-cycle and retired-API-version checks ([ADR 0013](docs/adr/0013_FRAMEWORK_PACKS.md)).
- **GitHub (optional):** your own GitHub App ([setup](docs/GITHUB.md), [ADR 0015](docs/adr/0015_GITHUB_REVIEWS.md)).
  - Verified linking, then every push to the default branch and every pull request is reviewed against the previous commit or the merge base.
  - Captures are checked against the commit, so nothing can be hidden from review.
  - Unchanged files reuse their results.
  - Optionally posts one check and one summary comment, and opens pull requests for checked fixes. It never merges.
- **Fixes:** automatic, reviewable fixes for selected findings: ESLint safe fixes, Java string comparison and retired Salesforce API versions ([ADR 0014](docs/adr/0014_VALIDATED_FIXES.md)).
  - Each fix is bound to its upload and shown as a diff with what to watch.
  - Reviewers can edit it. Changes that silence checks or weaken tests are refused.
  - It is checked on a copy: it applies, parses, the finding is gone and nothing new appears.
  - It downloads as a Git patch plus a JSON summary.
  - Project tests and builds are shown as "not run"; they are never executed.
- **Fix workspaces:** many fixes on top of one upload, which is never changed ([ADR 0016](docs/adr/0016_FIX_WORKSPACES.md)).
  - Fix selected issues or every issue of a rule automatically, or edit files by hand (add and delete files too).
  - Markers that hide problems and skipped or weakened tests are flagged; a hidden problem is never counted as fixed.
  - **Check my changes** re-reviews a copy and reports fixed, still present, hidden and new problems.
  - Compare each file (side by side or inline) and any two uploads.
  - Download a patch (`git apply`), one commit (`git am`), only the changed files, the full project or a summary.
- **Modern UI:** dark-first "deep space" design with a light theme, overview, upload with progress, live review progress, charts and source viewer.
- Loopback-only, token-authenticated local deployment: FastAPI API, Temporal worker, React UI, PostgreSQL 18, Temporal dev server.
- Readiness that checks the real dependencies (database + schema revision, Temporal namespace, worker pollers, artifact-store write/read probe), shown in the UI and by `make doctor`.
- A diagnostic Temporal workflow that proves API → Temporal → worker → artifact store/DB execution.
- Workspace-scoped projects (create/list/get) with database-enforced scope-compatible foreign keys for sources, snapshots and scans.
- Filesystem artifact store with symlink/traversal containment; artifacts live outside the repository.
- Generated API contract (`packages/contracts`) consumed by a typed web client; drift is checked.
- Developer commands (`make bootstrap|dev|doctor|migrate|seed-fixtures|check|test|test-e2e|benchmark|package|context-map|context-pack`).
- Planned features appear in the UI navigation as disabled items with the phase that delivers them.

## Deployment

Repository: <https://github.com/Vikaschd04/Ai_Code_Reviewer>. Free with a public URL: the Render Blueprint `render.yaml` (Render free web service + free PostgreSQL; lite profile — every feature, one scan at a time, sleeps when idle). Free alternative: **GitHub Codespaces** (starts automatically, private URL). Paid, always on: `deploy/render-standard.yaml`. All are a single-user mode signed in with one access token, plus an optional shared demo account (`CRP_DEMO_ENABLED`, on in the free Blueprint); every push to `main` runs CI, which also runs the app under the Render free limits (512 MB, 0.1 CPU). Setup steps, free-tier limits and the security posture: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Quickstart (macOS, verified)

```sh
brew install uv pnpm postgresql@18 temporal   # plus Python 3.14, Node 22 and a Java 17+ runtime (for PMD)
make bootstrap        # also downloads and verifies PMD, Opengrep, Trivy + its offline DB (~1.3 GB)
make dev
# other terminal: uv run crp-dev token --show, then open http://127.0.0.1:5173
# create a project, then drop a ZIP on "Add source" (or use crp-runner capture)
```

Full instructions, configuration and troubleshooting: [INSTALLATION.md](docs/INSTALLATION.md). Versions and licenses: [TOOLCHAIN.md](docs/TOOLCHAIN.md).

## Repository layout

| Path | Contents |
|---|---|
| `apps/web` | React + TypeScript UI (Vite), unit tests and Playwright E2E |
| `services/api` | FastAPI control plane (`crp-api`) |
| `services/worker` | Temporal worker (`crp-worker`) |
| `packages/core` | Settings, models, Alembic migrations, artifact store, workflow contracts |
| `packages/analysis` | Scope policy, ZIP validation, manifests, inventory, Tree-sitter structure, PMD/ESLint adapters, rule catalog |
| `engines/eslint-runner` | Isolated ESLint analyzer with the platform's trusted configuration |
| `fixtures` | Synthetic test projects (seeded and clean) |
| `packages/contracts` | Generated OpenAPI JSON and TypeScript types |
| `tools/devtools` | `crp-dev` developer CLI and real-infrastructure pytest fixtures |
| `tools/local-runner` | `crp-runner ping` and `crp-runner capture` (local folder snapshot + upload) |
| `docs/` | Specification, ADRs, status, memory and validation reports |

## Planned user journey

Create project → upload ZIP or capture a selected folder → inspect scope → start scan → see real progress/coverage → inspect code mappings and issues → investigate with AI when configured → generate/validate/export a patch → optionally connect Git later. See [roadmap](docs/ROADMAP.md), [features](docs/FEATURE_MATRIX.md) and [source intake](docs/SOURCE_INTAKE.md).

## Tests

`make check` (lint, format, strict types, contract drift), `make test` (unit + integration against real ephemeral PostgreSQL/Temporal), `make test-e2e` (browser tests against an isolated stack).

## Limitations

Local development plus a single-user hosted mode (Render free or GitHub Codespaces; paid Render later); not a multi-tenant or SSO deployment. Engines run without a per-scan OS sandbox. The Linux container image is built and smoke-tested in CI; Windows is untested. Temporal runs as the single-node dev server. Graph relations are syntax-level (no classpath or type checker). Fix checks are source-level (no isolated test/build runner yet), and AI review has not yet been measured against a live model. Next: the owner's AI key (P03 live evaluation, then AI patches) and GitHub App (P06 live check); then the isolated test/build runner (P05) and Phase 7 production hardening. See [known issues](docs/memory/KNOWN_ISSUES.md).

## Project quality

See [architecture](docs/ARCHITECTURE.md), [coding standards](docs/CODING_STANDARDS.md), [test strategy](docs/TEST_STRATEGY.md), [security](SECURITY.md), [completion criteria](docs/DEFINITION_OF_DONE.md) and [current phase](docs/PHASE_STATUS.md).
