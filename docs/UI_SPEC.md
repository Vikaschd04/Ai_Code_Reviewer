# User experience specification

## Implemented in P00

Sign-in (local token → HttpOnly session), Overview (live readiness table with icon+text status, workflow diagnostic with an explicit "no code analysis" note, scope statement), Projects (list with "Synthetic fixture" labels, create form), capability-driven navigation where future screens are disabled with their phase and reason. Semantic tables with captions and row headers, labelled controls, `aria-live` regions, focus moved to the page heading on navigation, light/dark colour schemes. Verified by vitest, Playwright E2E and screenshots (P00 report).

## Implemented in P01 (redesign)

Dark-first "deep space" design system (glass surfaces, gradient accents, grid backdrop) with a light theme toggle that respects the OS preference; sidebar navigation (Dashboard, Projects, Operations; planned capabilities greyed out with their phase), breadcrumb top bar, skip link, focus moved to each page's heading. Screens: dashboard (stat tiles, project cards with severity stacked bars), projects list/create, project workspace (overview with severity bars, ZIP drag-and-drop upload with byte progress and rejection details, local-runner command with copy button, snapshots, scans), snapshot scope review (identity, scope, language bars, technology indicators with confidence, manifest explorer with disposition filters, untrusted-instructions notice), scan (live pipeline via SSE, engine cards with coverage meters, severity and category charts, limitations, findings with severity/engine/category/search filters, coverage table), finding detail (highlighted source span, masked secrets, rule guidance, evidence), operations (readiness + diagnostic). Severity uses fixed status colours plus distinct glyphs and text labels; charts use 2 px segment gaps, legends with counts and tooltips; `prefers-reduced-motion` disables animation; `forced-colors` adds outlines.

## Implemented in P02

Scan page: six-stage pipeline (Structure, Graph, PMD, ESLint, Opengrep, Trivy), engine cards with rule counts, cache reuse ("N reused · M run" or "not cacheable") and offline vulnerability-DB age/staleness; findings table with dependency locations (`pom.xml · dependency`, never an invented line), "also reported by" correlation, issue status column and issue-status filter; coverage rows mark cached results; header actions JSON/SARIF download, Re-run (cache reuse) and Full rescan; Compare tab (selectable base scan, six group tiles with explanations, per-group table with reasons, engine compatibility table, notes). Finding page: dependency panel (package, installed, fixed-in, advisory, status, PURL), correlated findings, guidance source labelled (catalog or offline Trivy DB), triage panel (status, owner, reason, exception expiry ≤ 1 year, optimistic version, history timeline; locked with an explanation for RESOLVED/FIX_PROPOSED). Project page: Issues tab (status tiles as filters, recheck-state filters with counts, search, paged table linking to the latest finding) and Architecture tab for the latest snapshot. Snapshot page: "Scope & files" and "Architecture" tabs. Architecture view: build tiles, edge-classification legend (line style + icon + label + count), module map SVG (keyboard-focusable nodes, ≤24 modules) with a module-dependency table, node search, bounded radial neighborhood SVG (1–2 hops, kind glyphs, dashed/dotted lines for non-resolved edges) with a full relation table including evidence path/line/text, impact list with caveats, resolution-gap list. Reviewed in light and dark themes (E2E screenshots).

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

Snapshot identity is always visible; optional Git metadata supplements it. Display counts with meaningful denominators. Distinguish pending/failed/excluded/unsupported/unreviewed from clean. Severity and evidence confidence are separate. Risk acceptance/suppression is not a fix. Never show a secret value or absolute host path in a source snippet/error.

Use accessible labels, keyboard/focus behavior, semantic tables, color-independent status indicators, responsive layout and reasonable contrast. Provide source/diff line navigation without arbitrary HTML. Large file/results lists are virtualized or paginated; graph neighborhoods are bounded.

## E2E acceptance examples

Upload a valid mixed-language fixture and drill from an issue to its exact snapshot line. Reject a traversal archive with an understandable message. Simulate an engine crash and keep completed results while displaying incomplete coverage. Switch snapshots without retaining stale graph/source data. Cancel a scan and see an honest terminal state. Open the app without an AI key and still use the deterministic workflow.

