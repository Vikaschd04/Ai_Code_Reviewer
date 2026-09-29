# User experience specification

Product name: **refactorX** (user-facing everywhere; internal identifiers such as `crp_*` packages and `CRP_*` settings are unchanged — ADR 0011).

## Reviewer-first presentation (product rule for all current and future screens)

refactorX is built for a global audience of reviewers, team leads and developers, not for the people who operate it. Every screen, including future phases (AI investigation, fixes, Git, policies), follows these rules:

1. **Show what a reviewer needs to act:** what was found, how severe it is, where it is (file name first), why it matters, how to fix it, what changed since the last review and what the team decided.
2. **Plain language.** Use the product vocabulary in `apps/web/src/lib/labels.ts`: *review* (not scan), *upload* (not snapshot), check names such as "Security patterns" or "Dependencies & secrets" (not engine ids), "Complete / Partly complete / Waiting / In progress", "Fixed / Still present / Not rechecked", "Not a problem" (false positive), "Acknowledged" (triaged). Server text that says "scan" is reworded in the UI.
3. **Technical provenance is one click away, never in the way.** Engine and rule versions, rule-set ids, fingerprints, snapshot hashes, policy versions, cache statistics, compatibility tables and resolution details live in a collapsed **"Technical details"** disclosure (`Disclosure` component) — still available for audit, never deleted.
4. **Operational pages are for administrators only.** Service readiness and diagnostics appear under *Administration → System status* for operators; demo and member users never see them. Developer-only affordances (CLI commands, local runner, token file paths) appear only in the local development environment.
5. **Honesty is not simplified away:** partial results, files that could not be checked and limitations are still stated — in plain words, with the detail behind the disclosure.
6. **Every screen is checked visually** in light, dark and mobile (390 px, no horizontal page scroll) by `apps/web/e2e/ui-tour.spec.ts` screenshots before a change is complete.

## Implemented in P00

Sign-in (local token → HttpOnly session), Overview (live readiness table with icon+text status, workflow diagnostic with an explicit "no code analysis" note, scope statement), Projects (list with "Synthetic fixture" labels, create form), capability-driven navigation where future screens are disabled with their phase and reason. Semantic tables with captions and row headers, labelled controls, `aria-live` regions, focus moved to the page heading on navigation, light/dark colour schemes. Verified by vitest, Playwright E2E and screenshots (P00 report).

## Implemented in P01 (redesign)

Dark-first "deep space" design system (glass surfaces, gradient accents, grid backdrop) with a light theme toggle that respects the OS preference; sidebar navigation (Dashboard, Projects, Operations; planned capabilities greyed out with their phase), breadcrumb top bar, skip link, focus moved to each page's heading. Screens: dashboard (stat tiles, project cards with severity stacked bars), projects list/create, project workspace (overview with severity bars, ZIP drag-and-drop upload with byte progress and rejection details, local-runner command with copy button, snapshots, scans), snapshot scope review (identity, scope, language bars, technology indicators with confidence, manifest explorer with disposition filters, untrusted-instructions notice), scan (live pipeline via SSE, engine cards with coverage meters, severity and category charts, limitations, findings with severity/engine/category/search filters, coverage table), finding detail (highlighted source span, masked secrets, rule guidance, evidence), operations (readiness + diagnostic). Severity uses fixed status colours plus distinct glyphs and text labels; charts use 2 px segment gaps, legends with counts and tooltips; `prefers-reduced-motion` disables animation; `forced-colors` adds outlines.

## Implemented in P02

Scan page: six-stage pipeline (Structure, Graph, PMD, ESLint, Opengrep, Trivy), engine cards with rule counts, cache reuse ("N reused · M run" or "not cacheable") and offline vulnerability-DB age/staleness; findings table with dependency locations (`pom.xml · dependency`, never an invented line), "also reported by" correlation, issue status column and issue-status filter; coverage rows mark cached results; header actions JSON/SARIF download, Re-run (cache reuse) and Full rescan; Compare tab (selectable base scan, six group tiles with explanations, per-group table with reasons, engine compatibility table, notes). Finding page: dependency panel (package, installed, fixed-in, advisory, status, PURL), correlated findings, guidance source labelled (catalog or offline Trivy DB), triage panel (status, owner, reason, exception expiry ≤ 1 year, optimistic version, history timeline; locked with an explanation for RESOLVED/FIX_PROPOSED). Project page: Issues tab (status tiles as filters, recheck-state filters with counts, search, paged table linking to the latest finding) and Architecture tab for the latest snapshot. Snapshot page: "Scope & files" and "Architecture" tabs. Architecture view: build tiles, edge-classification legend (line style + icon + label + count), module map SVG (keyboard-focusable nodes, ≤24 modules) with a module-dependency table, node search, bounded radial neighborhood SVG (1–2 hops, kind glyphs, dashed/dotted lines for non-resolved edges) with a full relation table including evidence path/line/text, impact list with caveats, resolution-gap list. Reviewed in light and dark themes (E2E screenshots).

## Implemented for refactorX (30 September 2026)

Sign-in page with product introduction, **Try the demo** (shared demo workspace, no token) and access-token sign-in with an environment-appropriate hint. Overview: welcome, "Try the sample project" (one-click dry run: create the sample, freeze it, review it) and "Review your own code" cards when empty; otherwise stat tiles (projects, findings, critical & high, reviews) and project cards without hashes. Demo banner on every page for the demo account. Projects: list (name, sample badge, description, created) plus create form and sample card. Project: tabs Overview · Issues · Architecture · Reviews (numbered "Review 1…") · Uploads · Upload code; upload success offers **Start review**. Upload page: files/languages/technologies cards, file list with Reviewed/Skipped filters and plain skip reasons, technical details disclosure. Review results: progress steps with check names, severity and type charts, "What was checked" disclosure (per-check coverage, limitations, nested technical details), findings with file-name-first locations and merged duplicates ("Also found by…"), Files checked, Changes (New / Fixed / Still present / Couldn't verify). Finding: code, why it matters, how to fix, vulnerable-library card, decision panel with readable history, technical details. Architecture map: files/parts/connections, modules map with an optional table, search, neighbourhood and impact, technical details. System status (administrators): plain headline, service table, advanced pipeline test. Mobile: compact top navigation, two-column tiles.

## First journey

Projects → New source → ZIP upload or Local folder instructions → scope review → Scan → progress/coverage → overview/issues → evidence. Local folder instructions explain how the local runner captures and uploads bytes; no misleading text field that promises remote laptop access.

## Screens

| Screen | Required content and actions |
|---|---|
| Projects | Owner, last snapshot/scan, capability status, real issue/coverage counts |
| Add source | Modes, limits, validation errors, privacy/egress explanation and retry |
| Scope review | Submitted/captured files, exclusions/reasons, languages/versions, unsupported formats |
| Scan progress | Stage/engine states, completed/remaining scope, failures, cancellation |
| Repository overview | Modules, technologies, entry points, inventory, analysis-level limitations |
| Issues | Filter by category/severity/evidence/engine/file/owner/status and new/baseline |
| Issue detail | Source span, triggering conditions, evidence, impact, rule/version, recommendation |
| Architecture | Bounded graph, relationship provenance, source navigation and table equivalent |
| AI investigation | Question, cited answer, uncertainty, budget/provider availability |
| Fix workbench | Original/suggested diff, scope, validation checks and patch download |
| Policies/operations | Enabled rules/versions, exceptions, budgets, runners and failures |

Navigation and buttons must correspond to available phases. A disabled future capability explains why; do not show fabricated charts or active buttons with no backend.

## Display rules

Each review names the upload it reviewed (file name and date); the exact snapshot fingerprint is always available under Technical details, and optional Git metadata supplements it. Display counts with meaningful denominators. Distinguish pending/failed/excluded/unsupported/unreviewed from clean. Severity and evidence confidence are separate. Risk acceptance/suppression is not a fix. Never show a secret value or absolute host path in a source snippet/error.

Use accessible labels, keyboard/focus behavior, semantic tables, color-independent status indicators, responsive layout and reasonable contrast. Provide source/diff line navigation without arbitrary HTML. Large file/results lists are virtualized or paginated; graph neighborhoods are bounded.

## E2E acceptance examples

Upload a valid mixed-language fixture and drill from an issue to its exact snapshot line. Reject a traversal archive with an understandable message. Simulate an engine crash and keep completed results while displaying incomplete coverage. Switch snapshots without retaining stale graph/source data. Cancel a scan and see an honest terminal state. Open the app without an AI key and still use the deterministic workflow.

