# Market analysis: fixing in the portal, compiling, comparing, and architecture intelligence

Prepared 8 October 2026 from the owner's two new requirements (see "Requirements" below). Method:
- Public product documentation, vendor announcements and independent write-ups found through web
  research between 1 and 8 October 2026 (sources at the end).
- No hands-on trials of the commercial products.
- Vendor claims are reported as claims. Third-party descriptions are labelled as such.

This document feeds [ROADMAP.md](../ROADMAP.md) (phases P08–P11) and
[ARCHITECTURE_INTELLIGENCE.md](../ARCHITECTURE_INTELLIGENCE.md).

## Requirements (owner, 8 October 2026)

1. **Fix in the portal.** After a review, a user fixes issues inside refactorX:
   - by hand, or with AI help;
   - one issue or many at once;
   - then exports the result as a patch, or as only the changed files, for their IDE, repository
     or deployment.

   They can also:
   - **compile** where possible;
   - **compare** old and new versions of files in a simplified, Git-like view.
2. **Architecture intelligence (next phase).** After an upload, refactorX understands the whole
   codebase's architecture and suggests how to make it more efficient, scalable and fast, with
   production-grade fixes and help applying them.

## A. Fixing issues inside a portal

### How leading tools do it

| Tool | What it does | How a fix is verified | How it is delivered |
|---|---|---|---|
| SonarQube AI CodeFix | Sends the affected code and the issue to an LLM and proposes an edit, for a **select set of rules** (Java, JavaScript/TypeScript, Python, C#, C++, HTML, CSS). Hosted models or your own (Azure OpenAI, AWS Bedrock, OpenAI-compatible self-hosted gateways) [1][2] | Re-analysis in the normal workflow | Suggestion in the UI or IDE; the developer applies it |
| GitHub Copilot agentic autofix (public preview, July 2026) | Assigning an alert starts a cloud agent. It explores the codebase, proposes a fix, **re-runs CodeQL to confirm the alert closes**, and iterates [3] | Original detector re-run; iterates if still open | Draft pull request with a summary and the validation steps. Needs GitHub Code Security and Copilot [3] |
| Snyk Agent Fix (formerly DeepCode AI Fix) | Generates **up to five candidate fixes** with explanations [4] | Each candidate is re-scanned with Snyk Code for "fixed and nothing new"; a third-party write-up also describes a sandboxed unit-test pass in its retry loop [4][5] | IDE / PR |
| OpenRewrite / Moderne | **Deterministic recipes** on a lossless semantic tree that keeps formatting; mass refactoring across many repositories. Engine and core Java recipes are Apache-2.0; other recipes are source-available or proprietary [6][7] | Recipe semantics, plus the customer's build | Commits / PRs at scale |
| Amazon Q Developer code transformation | Java 8/11 → 17/21 upgrades from the CLI or IDE [8] | **Builds locally**, running unit and integration tests, to verify each step [8] | Changed code in the workspace |
| GitHub Copilot app modernization (GA for Java and .NET) | Assessment (AppCAT), upgrade plan, transformations including OpenRewrite, CVE checks [9][10] | **Iterates through build and test fixes**; full audit trail [9] | Reviewed changes per step |
| Salesforce ApexGuru | Runtime-aware detection of more than 24 Apex performance antipatterns, agentic fixing, and an MCP tool in the Salesforce DX MCP server [11][12] | Org runtime data informs severity [11] | VS Code / MCP clients |

### What this tells us

1. **Deterministic first, AI second, one verification loop.** Every serious product re-runs the
   original detector on the proposed change. refactorX's P05 ladder already does this per file.
   The market extends it to multi-file changes and, where an environment exists, to build and
   tests.
2. **Several candidates plus human choice** (Snyk) beats a single unreviewed patch.
3. **Delivery is pull-request-centric.** Upload-first users (ZIP, folder) have no PR, so they need
   a *change set* holding many fixes, exported once as:
   - a patch;
   - only the changed files (with folder structure);
   - the full patched project.

   This is a gap refactorX can own.
4. **Build and test verification needs an execution environment** (local build for Amazon Q, cloud
   agents and sandboxes for Copilot and Snyk). Products are explicit about it; refactorX must stay
   explicit about what was *not* run.
5. **Framework depth is rare.** None of the general tools model SAP Commerce configuration
   (Spring XML, items.xml, ImpEx). Salesforce has ApexGuru, but only inside Salesforce's
   ecosystem.

### Compiling in the portal: what is safe

- **Compiling is not always inert:**
  - `javac` runs annotation processors found on the classpath **by default**; `-proc:none`
    disables them. A malicious dependency can execute code during compilation [13].
  - Maven build extensions, Gradle build scripts and npm lifecycle scripts also execute code.
- **Isolation options:**
  - **Firecracker microVMs**, each with its own kernel (E2B: Apache-2.0, self-hostable, around
    150 ms boot) [14][15];
  - **gVisor**, a user-space kernel that intercepts every system call (common default for managed
    sandboxes) [16][17].
- **Recommended tiers** (adopted in P09):
  - **No execution:** syntax and type checking that loads no project code (TypeScript `tsc`
    type-check; Java parse).
  - **Safe compile in a sandbox:**
    - Java with `javac -proc:none`, against dependencies fetched by a resolver that never runs
      build extensions;
    - TypeScript with `npm ci --ignore-scripts` and `tsc --noEmit`;
    - in each case no network except a package proxy, with resource caps.
  - **Full build and tests:** only in microVM sandboxes, or the customer's own CI (through a P06
    pull request), or a customer-hosted runner.
  - Apex needs an authorized Salesforce org (check-only deploy); SAP Commerce needs a licensed
    build.

### Comparing versions (diff UX)

| Option | License | Size | Notes |
|---|---|---|---|
| Monaco diff editor (the VS Code editor) | MIT | about 2–5 MB [18] | Familiar VS Code look; heavy on mobile |
| CodeMirror 6 + merge view | MIT | modular core around 300 KB [18][19] | Lighter, mobile-friendly, tree-shakeable |

Recommendation: CodeMirror 6 for editing and comparing, keeping the existing lightweight `DiffView`
for read-only patches. Confirm exact versions and licenses at adoption.

## B. Architecture intelligence

### How leading tools do it

| Tool | Evidence used | What it produces |
|---|---|---|
| CAST Imaging | Semantic analysis of source across 150+ technologies [20] | Interactive maps; an **MCP server (GA)** that gives AI agents precise architectural context for debt remediation, modernization and impact analysis [20][21] |
| CAST Highlight | Fast portfolio code scans [22] | **Cloud-readiness "blockers and boosters"**, complexity and required changes; imported into Azure Migrate [22] |
| vFunction | Static plus runtime ("architectural observability") [23] | Architectural technical debt and decomposition advice, including circular dependencies between services, multi-hop call paths and services sharing databases [23] |
| CodeScene | **Git history** (behaviour) plus code health [24] | **Hotspots** (frequently changed, low-health files), **change coupling** (files that change together), team knowledge [24][25] |
| SonarQube Server 2026.4 (Structure101 acquired October 2024) | Static structure [26][27] | Automatic architecture map; a declared **intended architecture**, with deviations reported on every analysis [27] |
| ArchUnit (Apache-2.0), jQAssistant (GPLv3), Sonargraph, dependency-cruiser, ArchUnitTS | Static structure [28] | **Architecture rules as code**: layers, allowed dependencies, cycles |
| Konveyor (CNCF sandbox) with Kai | analyzer-lsp with 2,400+ community rules [29] | Modernization issues; **Kai** uses retrieval-augmented generation over static analysis and past migrations, model-agnostic [29][30] |
| AWS Transform / GitHub Copilot app modernization | Static assessment plus builds [8][9] | Assessment, plan, transformation, build/test loop |
| DeepWiki (Cognition) | Repository content [31] | AI architecture overviews, diagrams and chat **with line-level citations**. Limitations: no design rationale or ADRs; cached, so it can lag [31] |
| Sentry, perf-sentinel, Digma | **Runtime traces** (spans, OpenTelemetry) [32][33] | N+1 and other I/O antipatterns. Span-based detection cannot tell a real N+1 from intentional fan-out, which causes false positives [32] |
| Salesforce ApexGuru | Org runtime data plus code [11] | Apex performance antipatterns and fixes [11][12] |

### What this tells us

1. **Three kinds of evidence, never confused:**
   - *structure* (code and configuration);
   - *behaviour* (Git history: hotspots, change coupling);
   - *runtime* (traces, APM).

   Static analysis alone can only flag **potential** performance problems. Runtime tools confirm
   them.
2. **Intended versus actual architecture as code** is the standard control (Sonar, Sonargraph,
   ArchUnit).
3. **AI works on top of a precise model, not raw code** (CAST MCP, Konveyor RAG, DeepWiki
   citations). refactorX's snapshot graph (P02/P04) is that model; AI recommendations must cite
   graph facts and code.
4. **Recommendations need numbers and priority** (CodeScene hotspots, vFunction debt), not generic
   advice.
5. **Modernization is a pipeline:** assess → plan → deterministic transform → build/test verify →
   PR. It reuses P05 (fixes), P08 (change sets), P09 (builds) and P06 (PRs).

### Where refactorX can be different

- **Upload-first and Git:** one flow from findings → architecture → fix workspace → verification
  → patch, ZIP or PR, for teams without GitHub too.
- **Configuration-driven architecture for SAP Commerce and Salesforce** (P04 packs). General tools
  read code; refactorX also reads Spring wiring, item types, ImpEx, triggers, Flows and permission
  sets.
- **Honest evidence classes** on every claim (static, behavioural, runtime, AI hypothesis) and on
  every verification (what ran, what did not).
- **Recommendations tied to fixes:** each suggestion links to evidence, affected components and,
  where possible, a recipe or AI change in the fix workspace, verified by re-analysis and compile.

## C. Risks and honest limits

- **Runtime confirmation** of performance and scalability needs the customer's telemetry
  (OpenTelemetry/APM exports) or load tests. Without it, findings stay "potential".
- **Compile and build** need isolated compute that the free hosting plan cannot run. Paid
  infrastructure is the owner's decision.
- **AI quality** (fixes, architecture narratives) is unmeasured until the owner's provider key is
  configured (K-P03-01).
- **Architecture smell accuracy** needs a labelled evaluation set and expert review before strong
  claims.
- **Licensing:** OpenRewrite recipes beyond the Apache-2.0 core are source-available or
  proprietary [6]. jQAssistant is GPLv3 [28]. Check each component's exact license and version
  before adoption.

## D. Recommendations (adopted in the roadmap)

1. **P08 Fix workspace.**
   - Multi-file change sets from recipes, manual edits and AI candidates, with provenance per
     change.
   - Re-check of the change set.
   - Simplified compare views.
   - Exports: patch, changed files only, full project, summary, PR.
2. **P09 Isolated build.** The tiered compile and build model above; it also completes P05's test
   and build steps.
3. **P10 Architecture intelligence:**
   - architecture model and metrics;
   - intended-architecture rules;
   - smell, performance and scalability catalogs with evidence classes;
   - Git-history hotspots and change coupling;
   - optional runtime-evidence import;
   - an AI architect grounded on graph facts;
   - prioritized recommendations.
4. **P11 Architecture remediation:** guided refactorings (deterministic recipes first, then AI
   multi-file changes) verified by re-analysis, architecture rules and builds; migration plans.
5. **P07 Production hardening** stays the release gate before external customers.

## Sources

1. [SonarQube Server 2026.1 — AI CodeFix](https://docs.sonarsource.com/sonarqube-server/2026.1/ai-capabilities/ai-codefix)
2. [SonarQube Cloud — AI CodeFix](https://docs.sonarsource.com/sonarqube-cloud/ai-capabilities/ai-codefix)
3. [GitHub Changelog — Agentic autofix for code scanning alerts in public preview (10 July 2026)](https://github.blog/changelog/2026-07-10-agentic-autofix-for-code-scanning-alerts-in-public-preview/)
4. [Snyk docs — Fix code vulnerabilities automatically](https://docs.snyk.io/scan-with-snyk/snyk-code/manage-code-vulnerabilities/fix-code-vulnerabilities-automatically)
5. [Safeguard — How Snyk Agent Fix's retry loop self-corrects (third-party)](https://safeguard.sh/resources/blog/how-snyk-agent-fixs-agentic-retry-loop-self-corrects-failed-fix-attempts)
6. [Moderne — OpenRewrite](https://moderne.ai/openrewrite)
7. [OpenRewrite on GitHub](https://github.com/openrewrite/rewrite)
8. [Amazon Q Developer — Transforming code on the command line](https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/transform-CLI.html)
9. [Microsoft Learn — GitHub Copilot modernization overview](https://learn.microsoft.com/en-us/azure/developer/github-copilot-app-modernization/overview)
10. [ADTmag — GitHub Copilot-powered modernization for Java and .NET](https://adtmag.com/articles/2025/11/04/github-copilot-powered-modernization-now-available.aspx)
11. [Salesforce Developers — The ApexGuru AI engine, explained](https://developer.salesforce.com/blogs/2025/06/the-apexguru-ai-engine-explained)
12. [Salesforce Developers — Performance-first Apex development with ApexGuru in the DX MCP server](https://developer.salesforce.com/blogs/2026/04/performance-first-apex-development-with-apexguru-in-salesforce-dx-mcp-server)
13. [OpenJDK JDK-8306819 — Consider disabling the compiler's default active annotation processing](https://bugs.openjdk.org/browse/JDK-8306819)
14. [Northflank — E2B vs Modal (2026)](https://northflank.com/blog/e2b-vs-modal)
15. [Spheron — AI agent code execution sandboxes: E2B, Daytona and Firecracker (2026)](https://www.spheron.network/blog/ai-agent-code-execution-sandbox-e2b-daytona-firecracker/)
16. [gVisor](https://gvisor.dev/)
17. [Fly.io — Firecracker vs gVisor](https://fly.io/learn/firecracker-vs-gvisor/)
18. [PkgPulse — Monaco Editor vs CodeMirror 6 vs Sandpack (2026)](https://www.pkgpulse.com/guides/monaco-editor-vs-codemirror-6-vs-sandpack-in-browser-2026)
19. [Replit — Betting on CodeMirror](https://blog.replit.com/codemirror)
20. [CAST — AI can now understand and transform big enterprise codebases](https://www.castsoftware.com/news/ai-can-now-understand-and-transform-big-enterprise-codebases)
21. [CAST — MCP server](https://www.castsoftware.com/mcp)
22. [Microsoft Learn — Integrate CAST Highlight reports in Azure Migrate](https://learn.microsoft.com/en-us/azure/migrate/cast-highlights-integration?view=migrate)
23. [vFunction — Introducing architecture governance](https://vfunction.com/blog/introducing-architecture-governance/)
24. [CodeScene docs — Hotspots](https://codescene.io/docs/guides/technical/hotspots.html)
25. [CodeScene — Change coupling](https://codescene.com/blog/change-coupling-visualize-the-cost-of-change)
26. [Sonar — Sonar acquires Structure101](https://www.sonarsource.com/company/press-releases/sonar-acquires-structure101-to-strengthen-code-quality-offering/)
27. [Sonar — Manage your architecture on SonarQube Server](https://www.sonarsource.com/developers/blueprints/manage-your-architecture-on-sonarqube-server/)
28. [doubleSlash — Software architecture quality assurance (ArchUnit, jQAssistant)](https://blog.doubleslash.de/en/software-technologien/software-architecture-quality-assurance/)
29. [CNCF — Konveyor AI: supporting application modernization](https://www.cncf.io/blog/2024/11/22/konveyor-ai-supporting-application-modernization/)
30. [Red Hat — New updates to Konveyor AI](https://www.redhat.com/en/blog/new-updates-konveyor-ai-use-ai-driven-application-modernization-without-fine-tuning-model)
31. [Codersera — DeepWiki complete guide (2026)](https://codersera.com/blog/deepwiki-complete-guide-2026/)
32. [Sentry docs — N+1 queries](https://docs.sentry.io/product/issues/issue-details/performance-issues/n-one-queries/)
33. [perf-sentinel](https://perf-sentinel.dev/)
