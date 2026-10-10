# Product simplification and the insights advisor

Status: implemented, 10 October 2026 (ADR 0022; validation in P12_REPORT). Owner request: keep only what users need, make the experience
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
| Architecture, NFR readiness, AI review tabs | **Merged into Insights**: Recommendations (default), NFR questionnaire, Architecture | One place to understand the system and what to improve |
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
portability**. They group the NFR questionnaire's nine aspects (docs/NFR_ASSESSMENT.md).

1. **Tools (always on, deterministic).** A curated guideline catalog turns evidence into
   recommendations: open issues by rule family (injection, secrets, vulnerable libraries,
   database work in loops, unbounded queries, swallowed errors, cycles, hubs, unstable
   dependencies, rule breaches, retired APIs), missing mechanisms that the questionnaire looks
   for (no monitoring or tracing, no health endpoints or pipeline, no tests, no fault handling),
   and missing team targets. Each recommendation has an area, a priority, why it matters, guided
   steps, and its evidence (issues, files with lines, missing signals, targets). "Start fixing"
   opens a fix workspace on its issues.
2. **Agent (optional, policy-gated, labelled AI).** The advisor agent receives the
   recommendations and their evidence as numbered facts, may read code with the read-only tools
   (masked excerpts, only when the project's admin switched AI on), and submits an improvement
   plan: ordered steps that each cite recommendation ids, facts or code lines.
3. **No hallucination, by construction.**
   - Every step must cite at least one known recommendation or fact, or a code anchor whose
     quoted text matches the file; otherwise it is rejected and counted.
   - Numbers in a step must appear in the facts it cites.
   - Recommendations, priorities and counts come only from the tools; the agent orders,
     explains and specifies.
   - Without AI, the product still gives the tool recommendations; the plan says what AI would
     add.

## Delivery order (each step tested; no other features)

1. Insight engine and guideline catalog (tools), API, and the Overview's "health by area" and
   "next steps".
2. Advisor agent: run kind `advisor`, `submit_plan` with citation and number checks, fake-model
   tests.
3. Navigation: six tabs, Insights page, merged Fixes and Uploads, Settings, one "Fix this" card,
   redirects, no "Coming soon".
4. Browser journeys updated to the new navigation; light, dark and mobile screenshots;
   documentation.
