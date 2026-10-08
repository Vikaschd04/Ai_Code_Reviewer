# Execute Phase 10 — Architecture intelligence

Read ARCHITECTURE_INTELLIGENCE.md, research/MARKET_ANALYSIS_2026.md §B, P02/P04/P06 reports,
ADR 0008 (graph), ADR 0013 (framework packs), ADR 0015 (Git history) and ADR 0012 (AI). The goal:
explain the whole codebase's architecture, measure it, and recommend evidence-backed improvements
for maintainability, performance and scalability.

## Deliver

1. **Architecture model** per snapshot:
   - inferred components and layers (generic plus SAP Commerce and Salesforce aware), editable by
     the team;
   - data-access map (entities, tables, SOQL objects, item types);
   - interfaces (HTTP, messaging, jobs, platform events).
2. **Metrics:** coupling, instability, abstractness, distance, cycles, size and complexity,
   modularity, data sharing. Behavioural metrics (hotspots, change coupling, knowledge
   concentration) come from Git history: P06 commits, or an imported `git log` for uploads.
   Missing evidence shows as "not available", never zero.
3. **Intended architecture as code.** Declare layers and allowed or forbidden dependencies in the
   portal (versioned, audited; YAML export and import). Every review reports deviations as
   findings with issue lifecycle and lineage.
4. **Catalogs** (ARCHITECTURE_INTELLIGENCE.md): structural smells, performance and
   scalability/resilience signals. Each signal has an evidence class:
   - structural signals are *potential*;
   - runtime-confirmed signals come only from imported evidence.

   Each catalog row has positive and negative fixtures and a sourced rationale.
5. **Optional runtime evidence import.** OpenTelemetry trace files or APM exports, bounded and
   validated, mapped to code and components. Repeated similar spans confirm N+1 and fan-out. No
   live agents in this phase.
6. **Recommendations.** Prioritized backlog items with:
   - evidence links;
   - affected components;
   - expected effect (direction and what is measured);
   - effort range;
   - confidence;
   - status;
   - remediation link where available (P11).
7. **AI architect** (P03 provider, project AI policy). An architecture review report and draft
   ADRs, produced through read-only tools over the model. Every claim cites fact ids or code;
   uncited claims are rejected; numbers come only from tools. Labelled AI.
8. **UI:**
   - architecture overview (plain summary and health per area);
   - C4-style views, DSM, cycle explorer, hotspot treemap and trends;
   - rules editor, recommendations board, AI review.

   Technical details collapsed. Light, dark and mobile.
9. **Exports:** architecture report (HTML/Markdown, PDF later), rules YAML, SARIF for violations
   and smells.

## Mandatory tests

- **Metric correctness:**
  - metrics match hand-computed values on fixtures;
  - cycles found with the minimal edge set suggested;
  - no history means not available (not zero);
  - renames and moves keep lineage.
- **Rule violations:** detected and lifecycle-tracked; exceptions with expiry; a rule change
  re-evaluates honestly.
- **Catalog accuracy:** each catalog row has positive and negative fixtures (Java, TypeScript, SAP
  Commerce, Salesforce); generated and test code is handled.
- **Runtime import:** malformed or oversized trace files refused; spans mapped; a fan-out versus
  N+1 distinction case.
- **AI architect:** grounded (fake and fixture provider), injection stays data, uncited claims are
  rejected, budgets hold.
- **Scale:** measured analysis time and memory on medium and large synthetic repositories; the
  free profile stays within 512 MB (or the feature is disabled with a reason).
- **Evaluation:** precision and recall per smell on the labelled set, recorded in the report.

## Completion

Produce P10_REPORT.md with measured accuracy and limits, plus ADRs for the model and the
recommendation scoring. AI quality needs the live provider (BLOCKED for that part only). Runtime
confirmation needs customer telemetry; without it, signals stay "potential".
