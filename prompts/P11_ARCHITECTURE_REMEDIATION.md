# Execute Phase 11 — Architecture remediation and modernization

Read P10_REPORT, P08_REPORT, P09_REPORT, ARCHITECTURE_INTELLIGENCE.md ("Remediation") and
research/MARKET_ANALYSIS_2026.md. Turn recommendations into verified changes that teams can take
away (patch, changed files, pull request).

## Deliver

1. **Deterministic refactorings** as recipes producing P08 change sets:
   - move class or package;
   - extract interface, dependency inversion;
   - introduce a facade;
   - split a module along a cluster;
   - add timeouts and retries to configured clients;
   - batch queries out of loops (supported patterns);
   - Salesforce bulkification and selector extraction (supported patterns);
   - SAP service and DAO extraction from interceptors (supported patterns).

   Reuse vetted engines where licensing allows (OpenRewrite Apache-2.0 core for Java; ts-morph or
   jscodeshift for TypeScript). Check each license and version.
2. **"What-if" simulation.** Recompute metrics, cycles and rule results on a virtual change before
   editing; show the expected movement.
3. **AI multi-file change plans** (P03 provider, project policy). Plan first (files, steps,
   risks), then candidates per step in the change set, labelled AI. Bounded files, lines, calls
   and spend.
4. **Verification per change set.**
   - The smell or violation is gone; no new findings or violations.
   - Metrics move in the expected direction.
   - Compile, build and tests pass where P09 is available (otherwise "not compiled", stated).
   - Results are bound to hashes.
5. **Migration plans** for decomposition and scalability work: ordered steps (strangler, data
   ownership moves, introduce asynchronous boundaries), each a recommendation with checks.
   Progress is tracked across reviews.
6. **Delivery** through P08 exports and P06 pull requests. Never auto-merged.

## Mandatory tests

- **Refactoring recipes:** each preserves behaviour on fixtures (compile and tests in the sandbox
  where available) and breaks the targeted cycle or violation without new ones.
- **Unsafe cases refused:** reflection, framework wiring by name (Spring XML / SAP bean ids),
  serialization or public API changes are refused or flagged.
- **What-if:** the predicted metrics equal the post-change re-analysis.
- **AI plans:** grounded and bounded; partial failure leaves a consistent change set.
- **Staleness:** a migration step is invalidated when the base changes; it is rebased and
  revalidated.

## Completion

Produce P11_REPORT.md with measured success rates per recipe and honest limits. Quality claims for
AI plans need the live provider and an evaluation set.
