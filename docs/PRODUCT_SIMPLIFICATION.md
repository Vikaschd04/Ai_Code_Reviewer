# Product simplification and the insights advisor

Status: implemented, 10 October 2026 (ADR 0022; validation in P12_REPORT). Update the same day
(ADR 0024): the NFR questionnaire was removed and the recommendations became NFR checkpoints —
Insights now has two views, NFR checkpoints and Architecture. Owner request: keep only what users need, make the experience
simple, and let tools and agents together analyse a project against NFR guidelines and guide its
improvement, without hallucination. Supersedes the separate "Architecture", "NFR readiness" and
"AI review" project tabs.

## What users come to do

1. **Bring code in** (upload or connect a repository) and get it reviewed.
2. **See what matters**: the problems, and how healthy the system is for security, reliability,
   performance and scalability, architecture, operations.
3. **Improve it**: fix problems safely, guided by clear recommendations.
4. **Track progress** across versions.

Everything on screen must serve one of these. Technical and administrative detail stays out of
the way (collapsed, or in Settings).

## Audit: keep, merge, hide

| Today | Decision | Why |
|---|---|---|
| Project tabs: Overview, Issues, Architecture, NFR readiness, AI review, Fix workspaces, Fixes, GitHub, Reviews, Uploads, Upload code (11) | **Six tabs: Overview, Issues, Insights, Fixes, Uploads, Settings** | Eleven tabs hid the journey; each new tab maps to one user goal |
| Architecture, NFR readiness, AI review tabs | **Merged into Insights**: NFR checkpoints (default), Architecture (the questionnaire view was removed by ADR 0024) | One place to understand the system and what to improve |
| AI "review selected files" | **Hidden** (API kept) | Overlaps the engines and the advisor; not a user goal on its own |
| AI policy (on/off, excerpt size) | **Moved to Settings** | Administrative |
| Fix workspaces and Fixes tabs | **Merged into Fixes** (workspaces; earlier single fixes collapsed, shown only if any exist) | Two ways to fix the same thing confused users |
| Finding page: single-fix card, workspace card, AI card | **One "Fix this" card** (workspace) and the AI check only when AI is on for the project | One clear next step per problem |
| GitHub tab | **Connection to Settings; GitHub reviews to Uploads** | Setup versus history |
| Reviews, Uploads, Upload code tabs | **Merged into Uploads** (upload, versions, reviews) | One history of the code |
| Sidebar "Coming soon" list | **Removed** (capabilities stay in System status for operators) | Promises are not features |
| Upload page tabs (What's inside, Architecture, Compare), review page tabs, workspace tabs | Kept | Contextual to one version or one change set |
| Old links (`?tab=architecture`, `nfr`, `ai`, `workspaces`, `github`, `reviews`, …) | Redirect to the new tab | Nothing breaks |

## Insights: tools first, agents on top

The advisor answers "how do I make this system better?" for six areas: **Security**,
**Reliability** (reliability, availability, recoverability), **Performance and scalability**,
**Architecture and maintainability**, **Operations and monitoring**, **Experience and
portability** (docs/NFR_ASSESSMENT.md).

1. **Tools (always on, deterministic).** 28 NFR checkpoints (ADR 0024) turn evidence into a
   status per requirement: open issues by rule family (injection, secrets, vulnerable libraries,
   insecure containers, single instances, missing probes, database work in loops, unbounded
   queries, swallowed errors, cycles, hubs, rule breaches, retired APIs), mechanisms found with
   file and line, mechanisms not found (monitoring, diagnostics, health checks, tests, fault
   handling, autoscaling, pipeline, accessibility checks, API descriptions), and checks that did
   not run. Each checkpoint has an area, a priority when it needs work, why it matters and how to
   resolve it. "Start fixing" opens a fix workspace on its issues, where recipes fix the
   configuration gaps; a missing mechanism can be marked as handled outside the code.
2. **Agent (optional, policy-gated, labelled AI).** The advisor agent receives the checkpoints
   that need work and their evidence as numbered facts, may read code with the read-only tools
   (masked excerpts, only when the project's admin switched AI on), and submits an improvement
   plan: ordered steps that each cite checkpoint ids, facts or code lines.
3. **No hallucination, by construction.**
   - Every step must cite at least one known checkpoint or fact, or a code anchor whose quoted
     text matches the file; otherwise it is rejected and counted.
   - Numbers in a step must appear in the facts it cites.
   - Statuses, priorities and counts come only from the tools; the agent orders, explains and
     specifies.
   - Without AI, the product still gives every checkpoint and how to resolve it.

## Delivery order (each step tested; no other features)

1. Insight engine and guideline catalog (tools), API, and the Overview's "health by area" and
   "next steps".
2. Advisor agent: run kind `advisor`, `submit_plan` with citation and number checks, fake-model
   tests.
3. Navigation: six tabs, Insights page, merged Fixes and Uploads, Settings, one "Fix this" card,
   redirects, no "Coming soon".
4. Browser journeys updated to the new navigation; light, dark and mobile screenshots;
   documentation.
