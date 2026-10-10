# Feature matrix

Rows are NOT_IMPLEMENTED unless a status is given. Replace with evidence-backed status as work proceeds. M = mandatory in that phase; C = conditional on explicit prerequisites. Unsupported capabilities remain visible.

| Feature | Phase | Gate |
|---|---|---|
| App shell/API/DB/workflow foundation | 0 M | Real health/migration/job checks — **IMPLEMENTED, gate passed (macOS)**: docs/validation/P00_REPORT.md |
| ZIP upload | 1 M | Streaming limits and malicious-archive tests — **IMPLEMENTED** (P01_REPORT) |
| Local CLI folder capture | 1 M | Original tree unchanged; snapshot manifest verified — **IMPLEMENTED** |
| Browser folder selection | 1 optional | Capability fallback; file paths normalized — NOT_IMPLEMENTED (P01-F1) |
| Registered server source | 1 optional | Admin allowlist and opaque ID — NOT_IMPLEMENTED (P01-F1) |
| Inventory and basic Java/JS syntax maps | 1 M | File accounting; parse failures visible — **IMPLEMENTED** (Tree-sitter syntax only) |
| PMD and ESLint | 1 M | Real output and negative fixtures — **IMPLEMENTED** (PMD 7.27.0 Java; ESLint 10.11.0 JS/TS) |
| Issue/coverage dashboard | 1 M | Real backend data and accessible states — **IMPLEMENTED** |
| Persistent graph and scan comparison | 2 M | Evidence/version correctness — **IMPLEMENTED** (P02_REPORT; syntax-level graph with resolved/declared/inferred/unresolved edges; strict recheck lifecycle) |
| Opengrep and Trivy | 2 M | Approved rules/images; partial states — **IMPLEMENTED** (Opengrep 1.30.0 owned rules; Trivy 0.69.3 offline vuln + secret; ADR 0007) |
| JSON/SARIF export | 2 M | Schema-valid output and access checks — **IMPLEMENTED** (own JSON Schema + official SARIF 2.1.0 validation) |
| Issue triage, exceptions with expiry, per-file engine cache | 2 M | Optimistic versions; invalidation by content/rules/config — **IMPLEMENTED** (ADR 0008) |
| Provider AI review and repository Q&A | 3 M | Source citations and budget/injection tests — **IMPLEMENTED offline; live-provider gate BLOCKED** (P03_REPORT; key not configured) |
| Alibaba OCR integration | 3 evaluated | Adopt or record evidence-based rejection and working alternative — **EVALUATED, not adopted** (P03_OCR_EVALUATION; direct-provider path delivered) |
| SAP Commerce and Salesforce packs | 4 M | Versioned domain fixtures and coverage — **IMPLEMENTED, experimental** (P04_REPORT; SAP 2105–2211, Salesforce API 31.0+; no SME review) |
| SAP build / Salesforce org validation | 4–5 C | Customer toolchain/environment — NOT RUN (conditional profiles defined in FRAMEWORK_ADAPTERS.md; fix validation reports them as not run) |
| Patch workbench and download | 5 M | Independent validation and input preservation — **IMPLEMENTED for deterministic fixes; source-level validation** (P05_REPORT; 3 recipe families; tests/build not run — no isolated runner; AI patches not implemented until the P03 provider is live) |
| GitHub/incremental/PR publication | 6 M/C | Auth, webhook and freshness gates — **IMPLEMENTED; verified against a labelled fake GitHub; live connector check BLOCKED** until the owner provides a GitHub App and test repository (P06_REPORT, ADR 0015) |
| Fix workspace: manual/AI/bulk fixes, compare, multi-file export | 8 M | Exports apply only to the exact base; provenance; honest re-check — **IMPLEMENTED for manual, recipe and AI fixes** (P08_REPORT, ADR 0016: editor, bulk recipes, AI candidates checked like recipe fixes (verified with the labelled test model; live quality needs the owner's key), re-check on Temporal and lite, compare files and uploads, patch / `git am` / changed-files ZIP / full ZIP / summary). one pull request per checked workspace for GitHub projects. Source-level (not compiled until P09) |
| Compile/build in the portal | 9 M/C | Tiered isolation; no project code in Tiers 0–1; containment tests — **Tier 0 IMPLEMENTED** (TypeScript type-check in fix workspace checks, nothing executed; P09_REPORT, ADR 0017). Tiers 1–2 BLOCKED on paid isolated compute |
| Architecture intelligence (model, metrics, rules, smells, performance/scalability signals, recommendations, AI architect) | 10 M | Measured accuracy; evidence classes; "potential" vs runtime-confirmed — **slices 1–2 IMPLEMENTED**: components, Martin metrics, cycles with cheapest cut, Structure health (ADR 0018); intended architecture as code — layers, forbid rules, expiring exceptions, versioned YAML, breaches as tracked issues with per-rule honest rechecks (ADR 0019); structural smells — cycles, unstable dependencies, hub-like parts, one issue per part, labelled evaluation (ADR 0020); see P10_REPORT. Performance/scalability catalogs, history, runtime evidence, recommendations and AI architect not yet |
| Architecture remediation and migration plans | 11 M | Verified refactorings; what-if equals re-analysis — NOT_IMPLEMENTED |
| NFR checkpoints (insight per non-functional requirement for the uploaded project, and help to resolve it) | 12 M | Honest statuses (not found, not checked and not applicable are never passed); cited evidence; detector precision and recall on labelled fixtures; no compliance certification — **IMPLEMENTED** (ADR 0024, replaces the questionnaire of ADR 0021): 28 checkpoints in six areas with statuses from issues, evidence and engine runs; stack-specific steps; Start fixing; recipes for the six configuration rules; "handled elsewhere" decisions; grounded advisor plan (live AI quality needs the owner's key). Configuration and infrastructure evidence (ADR 0023): engine `nfr` and Trivy's embedded misconfiguration checks (not Terraform). Code patterns (slice 3): 6 owned Opengrep rules for calls without timeouts, blocking reactive calls and unbounded thread pools |
| SSO/multi-tenant private execution | 7 M | Isolation and release qualification |

Every language/version has independent inventory, parse, resolve, rules, AI, build and runtime statuses. A single green language badge is insufficient. Demo mode must be visibly labelled and cannot satisfy mandatory engine integration gates.

