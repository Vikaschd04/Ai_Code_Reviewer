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
| Provider AI review and repository Q&A | 3 M | Source citations and budget/injection tests |
| Alibaba OCR integration | 3 evaluated | Adopt or record evidence-based rejection and working alternative |
| SAP Commerce and Salesforce packs | 4 M | Versioned domain fixtures and coverage |
| SAP build / Salesforce org validation | 4–5 C | Customer toolchain/environment |
| Patch workbench and download | 5 M | Independent validation and input preservation |
| GitHub/incremental/PR publication | 6 M/C | Auth, webhook and freshness gates |
| SSO/multi-tenant private execution | 7 M | Isolation and release qualification |

Every language/version has independent inventory, parse, resolve, rules, AI, build and runtime statuses. A single green language badge is insufficient. Demo mode must be visibly labelled and cannot satisfy mandatory engine integration gates.

