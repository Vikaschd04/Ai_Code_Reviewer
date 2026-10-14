# What users struggle with when analysing and refactoring code, and what refactorX should add

Date: 14 October 2026. Owner request: research the challenges users face while refactoring or
analysing code, and the add-on features that make refactorX more useful and scalable without
making it complicated; the portal should solve issues with AI agents, and solutions should work.
Builds on [MARKET_ANALYSIS_2026.md](MARKET_ANALYSIS_2026.md) (fix and architecture tools). Figures
are quoted with their source; vendor figures are marked as such.

## 1. What users struggle with (evidence)

| # | Challenge | Evidence |
|---|---|---|
| C1 | **AI output is "almost right"** and trust is falling | Stack Overflow 2025: 66% cite "AI solutions that are almost right, but not quite" as the top frustration, 45% say debugging AI code takes longer; 46% distrust AI accuracy (31% a year earlier) while 84% use or plan to use AI [1][2] |
| C2 | **Fear of breaking behaviour; no tests to protect it** | in a Microsoft survey of 328 engineers, respondents said refactoring should not be done without tests [3]; CACM "Barriers to refactoring": inadequate test suites, "without tests, changes will break things" [4]; developers avoid refactoring before releases [5] |
| C3 | **Reviewing AI changes costs time** | METR 2025 randomized trial: experienced developers were 19% slower with AI (they believed 20% faster), partly from reviewing and correcting suggestions; a 2026 follow-up is inconclusive [6][7]. Google's migrations: about 74% of changes LLM-generated, ~50% time saved by developer estimate, but reviewers still reverted some model edits [8][9] |
| C4 | **Copy-paste grows, refactoring shrinks** | GitClear (vendor) 2025: in 2024 copy/pasted lines exceeded moved (refactored) lines for the first time; duplicated blocks up sharply [10]. 2026 update: refactoring moves down 70% versus 2022 [11] |
| C5 | **More change, less stability** | DORA 2025: AI adoption goes with higher throughput and higher instability; AI amplifies the existing engineering system, and instability rises where automated tests and fast feedback are missing [12][13] |
| C6 | **No time or plan for debt** | Only 40% of agile practitioners address refactoring in planning (98% plan features) [4]; Stripe's 2018 survey put debt-related work at about a third of developer time (old figure, still widely quoted) [14] |
| C7 | **Upgrades and migrations are big, repetitive work** | Amazon (vendor): Java 17 upgrades from ~50 developer-days to hours, 79% of generated changes shipped without edits — so about 1 in 5 needed changes [15] |
| C8 | **Noise causes alert fatigue** | A 2025 qualitative study finds slow adoption of AI review tools over reliability and context accuracy [16]; JetBrains 2025: 44% used an AI review tool in the past year (via [17]) |
| C9 | **Passing tests is not proof a fix is right** | In an LLM-repair survey, differential testing found behaviour divergence in about 30% of patches that passed the tests, and ~29% of those were confirmed incorrect [18]. Meta's TestGen-LLM kept only tests that built, passed repeatedly and added coverage: about 75% built, 57% passed reliably, 25% added coverage [19] |

**What this means for refactorX:** users do not lack suggestions — they lack **confidence** that a
change is right, **time** to review it, and a **plan** for what to fix first. The winning product
shows evidence, verifies changes with several independent checks, keeps changes small and
reviewable, and says plainly what was not verified.

## 2. Where refactorX already stands

| Challenge | Already in refactorX |
|---|---|
| C1, C9 | Findings with evidence and provenance; AI answers and plans with deterministic citation and number checks; fix validation ladder (applies, parses, finding gone, nothing new); honest coverage ("not checked" is never "clean") |
| C2 | Changes only on copies (fix workspaces); TypeScript type-check without running code (P09 Tier 0) |
| C3 | Recipes with behaviour notes; side-by-side compare; one pull request per checked workspace |
| C5 | Workspace check re-runs every engine; GitHub check on pull requests |
| C6 | NFR checkpoints by area and priority; Start fixing per checkpoint |
| C7 | Retired Salesforce API version recipe; Trivy shows the fixed version of vulnerable libraries |
| C8 | Owned, tested rules; one issue per part for architecture smells; correlation across engines |

## 3. Add-ons, ranked (simple for users; each built on what exists)

### Tier 1 — no owner input needed

| Add-on | What the user sees | Why (challenge) | Effort |
|---|---|---|---|
| **A1. Fix everything safe for a checkpoint** | One button per checkpoint: "Fix 7 of 9 automatically", which opens a workspace with all recipes applied and the check already running | C3, C6 | Small (bulk recipes and checks exist) |
| **A2. Dependency upgrades as fixes** | For vulnerable libraries: "Upgrade log4j-core to 2.17.1" edits `pom.xml`/`package.json` to the fixed version Trivy names; the re-check confirms the vulnerability is gone; compile and tests shown as "not run" until A8 | C7, security checkpoint | Small–medium |
| **A3. Duplicate code detection** | A checkpoint "No copy-pasted logic" fed by PMD's CPD (already in the pinned PMD 7.27.0), with the copies side by side and an AI "extract shared function" proposal checked like every fix | C4 | Small–medium |
| **A4. "Fix first" queue** | One ordered list across checkpoints: severity × checkpoint priority × whether a safe fix exists (× change frequency once Git history exists, P10 slice 4) | C6 | Small |
| **A5. Reviewer summary of a workspace** | Plain summary for the pull request and the reviewer: what changed and why, which checks passed, which parts depend on the changed files (impact from the dependency map), what still needs a human | C3 | Small–medium |
| **A6. Health over time and a merge gate** | Checkpoint and issue trends across uploads; GitHub check "fails when a change adds a high issue or breaks a checkpoint" | C5 | Medium |
| **A7. Code tour for newcomers** | A one-page project brief: parts, layers, main flows and hotspots from the dependency map, with an optional AI narrative whose every sentence cites files (labelled AI) | Understanding legacy code (C2) | Medium (AI part needs the key) |

### Tier 2 — needs the owner's decisions (compute, AI key)

| Add-on | Why | Needs |
|---|---|---|
| **A8. Build and run tests on the copy** (P09 Tiers 1–2) | The single biggest step towards "fixes that work": compile and the project's own tests on every workspace check | Sandbox compute (paid, owner) |
| **A9. Safety-net tests before refactoring** | AI writes characterization tests for the code about to change; kept only if they build, pass repeatedly on the original and add coverage (Meta's filter [19]); then they guard the refactor | A8 + AI key |
| **A10. Multi-file refactoring agent** (P11) | Plan → small change sets → validation ladder → tests, one approved step at a time | A8 + AI key |
| **A11. Framework migrations** | Java 8/11 → 17/21, Spring Boot 2 → 3, javax → jakarta through deterministic recipes (OpenRewrite core recipes are Apache-2.0; some Moderne recipe packs use a source-available licence — check each before adoption) | A8; licence check |
| **A12. Mutation checks for AI fixes** | Small deliberate faults show whether tests would catch a wrong fix (Meta's ACH [19]); raises confidence beyond "tests pass" | A8 |

### Tier 3 — scale and team fit

- Jira or Azure Boards export of checkpoints and issues; Slack or Teams notices for new high issues.
- A read-only MCP server so developers' own AI assistants (Claude Code, Copilot) can read a
  project's checkpoints, issues and evidence — refactorX stays the source of truth.
- SSO, tenant isolation, retention and audit export (P07).

## 4. "100% working" — what we can honestly promise

No tool can guarantee that every AI-written change is correct: tests can pass while behaviour is
wrong [18], and even Amazon's best case needed changes in about 1 in 5 generated upgrades [15].
What refactorX can guarantee is stronger and checkable:

1. **Nothing reaches users unverified.** Every fix — deterministic or AI — goes through the
   validation ladder on a copy; failing candidates are dropped, not shown.
2. **Each fix states its verification level**, and the levels grow as checks are added:
   *Re-checked* (finding gone, nothing new — today) → *Type-checked* (TypeScript today) →
   *Builds* (A8) → *Tests pass* (A8) → *Behaviour pinned* (A9) → *Mutation-checked* (A12).
3. **Nothing is merged automatically.** A person approves every pull request, with the reviewer
   summary (A5).
4. **Deterministic first, AI second.** Recipes are used where the change is known; AI handles the
   rest and is held to the same checks.

With A8 and A9 in place, the share of offered fixes that work can be **measured** on the labelled
evaluation set and reported per release, instead of claimed.

## 5. Keeping it simple

- One place to start (Insights → checkpoints), one queue (A4), one workspace per upload, one
  button per checkpoint (A1).
- Advanced detail (rules, engines, verification steps) stays collapsed.
- Every add-on is optional and appears only when it applies (no Kubernetes → no Kubernetes
  checkpoints; no AI → no AI buttons).

## Recommended next steps

1. Tier 1 in this order: **A1 → A2 → A3 → A4 → A5** (each a small, testable slice, no owner input).
2. Owner decision on **A8** (sandbox compute): it unlocks A9–A12 and measurable "works" claims.
3. Measure AI fix quality per model on the labelled set: the free test model for development, the
   paid model for releases (ADR 0025).

## Sources

1. Stack Overflow — survey coverage: [The Register, 29 Jul 2025](https://www.theregister.com/2025/07/29/coders_are_using_ai_tools/); [VentureBeat](https://venturebeat.com/technology/stack-overflow-data-reveals-the-hidden-productivity-tax-of-almost-right-ai-code)
2. [InfoWorld: AI use grows but trust remains an issue](https://www.infoworld.com/article/4031673/ai-use-among-software-developers-grows-but-trust-remains-an-issue-stack-overflow-survey.html)
3. [Kim, Zimmermann, Nagappan — An Empirical Study of Refactoring Challenges and Benefits at Microsoft (TSE 2014)](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/kim-tse-2014.pdf)
4. [CACM — Barriers to Refactoring](https://cacm.acm.org/research/barriers-to-refactoring/)
5. [A Large-Scale Empirical Exploration on Refactoring Activities (SCP 2019)](https://www.sback.it/publications/scp2019.pdf)
6. [METR — Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/) ([paper](https://metr.org/Early_2025_AI_Experienced_OS_Devs_Study-paper.pdf))
7. [METR — Changing our developer productivity experiment design (Feb 2026)](https://metr.org/blog/2026-02-24-uplift-update/)
8. [Ziftci et al. — Migrating Code At Scale With LLMs At Google (FSE 2025)](https://arxiv.org/abs/2504.09691)
9. [How is Google using AI for internal code migrations? (2025)](https://arxiv.org/abs/2501.06972)
10. [GitClear — AI Copilot Code Quality 2025](https://www.gitclear.com/ai_assistant_code_quality_2025_research) (vendor)
11. [GitClear — The Maintainability Gap, 2026](https://www.gitclear.com/the_ai_code_quality_maintainability_gap) (vendor)
12. [DORA — 2025 year in review](https://dora.dev/insights/dora-2025-year-in-review/); [Balancing AI tensions](https://dora.dev/insights/balancing-ai-tensions/)
13. [RedMonk — DORA 2025: Measuring Software Delivery After AI](https://redmonk.com/rstephens/2025/12/18/dora2025/)
14. Stripe Developer Coefficient (2018), as summarised by [Tiny's technical debt whitepaper](https://www.tiny.cloud/technical-debt-whitepaper/)
15. [AWS DevOps blog — Amazon Q Developer milestone](https://aws.amazon.com/blogs/devops/amazon-q-developer-just-reached-a-260-million-dollar-milestone); [Slashdot summary of Andy Jassy's post](https://developers.slashdot.org/story/24/08/25/0049230/amazon-ceo-ai-assisted-code-transformation-saved-us-4500-years-of-developer-work) (vendor)
16. [Issues in Information Systems 26(2), 2025 — AI-driven code review adoption](https://iacis.org/iis/2025/2_iis_2025_346-360.pdf)
17. [The State of AI Code Review in 2026 (DEV Community)](https://dev.to/rahulxsingh/the-state-of-ai-code-review-in-2026-trends-tools-and-whats-next-2gfh) (secondary)
18. [A Survey of LLM-based Automated Program Repair (2025)](https://arxiv.org/abs/2506.23749)
19. [Alshahwan et al. — Automated Unit Test Improvement using LLMs at Meta (2024)](https://arxiv.org/abs/2402.09171); [Mutation-Guided LLM-based Test Generation at Meta (2025)](https://arxiv.org/abs/2501.12862)
