# Architecture intelligence specification (P10–P11)

Goal: after an upload or a reviewed commit, refactorX explains the codebase's architecture and
measures it. It suggests evidence-backed changes that make the system easier to change, faster and
more scalable, then helps apply and verify them.

Market context and sources: [research/MARKET_ANALYSIS_2026.md](research/MARKET_ANALYSIS_2026.md).
Phase prompts: [P10](../prompts/P10_ARCHITECTURE_INTELLIGENCE.md),
[P11](../prompts/P11_ARCHITECTURE_REMEDIATION.md).

## Principles

1. **Evidence classes on every claim**, never mixed silently:

   | Class | Source | What it can support |
   |---|---|---|
   | Structural | Code and configuration graph (P02 + P04 packs) | Dependencies, cycles, layers, sizes, rule violations, *potential* performance and scalability problems |
   | Behavioural | Git history (P06 commits, or an imported `git log`) | Hotspots, change coupling, knowledge concentration, trend |
   | Runtime | Imported OpenTelemetry traces, APM exports, load-test results | Confirmed latency hotspots, N+1 and fan-out patterns, call paths between services |
   | AI hypothesis | P03 provider grounded on the facts above | Narrative, trade-offs, design options. Always labelled; never a metric |

2. **Metrics are computed, never estimated by a model.** Every number links to the nodes, edges,
   commits or spans behind it.
3. **Intended versus actual.** Teams declare their intended architecture; deviations become
   findings with lifecycle (issues, exceptions, lineage).
4. **Potential is not confirmed.** A loop with a database call is a *potential* N+1 until runtime
   evidence shows it. Severity and confidence stay separate.
5. **Recommendations are backlog items, not essays.** Each has:
   - evidence;
   - affected components;
   - expected effect (direction, never invented percentages);
   - effort range;
   - confidence;
   - where possible, a remediation in the fix workspace (P08), verified by P09.

## Architecture model

Built per snapshot from the existing graph (modules, files, packages, types, functions,
framework components, relations with evidence) plus:

- **Components and layers.**
  - Inferred from modules, packages and naming conventions (`controller`/`service`/`repository`,
    `web`/`core`/`data`), then confirmed or edited by the team.
  - Framework-aware:
    - SAP extensions (storefront, facades, services, DAOs, interceptors, cron jobs);
    - Salesforce layers (triggers, handlers, services, selectors, LWC, Flows).
- **Data-access map.** Which components read or write which entities:
  - JPA entities, JDBC/SQL table names;
  - SOQL/DML objects;
  - FlexibleSearch item types;
  - ImpEx targets.

  It is the basis for shared-database and decomposition analysis.
- **Interfaces between components.** HTTP clients and controllers, messaging (JMS/Kafka
  producers and consumers), scheduled jobs, platform events.
- **Views.** C4-style context, container and component views, a dependency structure matrix (DSM),
  a cycle explorer, a hotspot treemap, and trends across reviews.

## Metrics

| Metric | Definition | Use |
|---|---|---|
| Afferent / efferent coupling (Ca / Ce) | Components depending on X / that X depends on | Hubs and fragile dependencies |
| Instability I = Ce / (Ca + Ce) | 0 stable … 1 unstable | Stable-dependencies principle |
| Abstractness A | Abstract types / all types in a component | Main-sequence distance |
| Distance D = \|A + I − 1\| | Distance from the "main sequence" (R. C. Martin) | Zones of pain and uselessness |
| Cycles | Strongly connected components of the component graph | Cycle-breaking candidates (minimum feedback edges) |
| Size and complexity | Lines, types and cyclomatic complexity per component (Tree-sitter) | God components |
| Modularity Q | Graph modularity of the declared or inferred partition | Decomposition quality |
| Hotspot score | Change frequency (commits) × complexity or code health | Where debt costs the most (behavioural) |
| Change coupling | Share of commits in which two files change together, above a support threshold | Hidden dependencies, shotgun surgery |
| Knowledge concentration | Commits per author per component | Bus-factor risk (behavioural) |
| Data sharing | Components writing the same entity or table | Decomposition blockers |

Metrics without the needed evidence (for example, no Git history for a ZIP upload) show as **not
available**, never as zero.

## Catalogs

### Structural architecture smells

| Smell | Detection | Typical recommendation |
|---|---|---|
| Cyclic dependency | Strongly connected component (more than one component) | Break the cycle at the weakest edge (interface extraction, dependency inversion, event) |
| Layer violation | Edge not allowed by the declared rules (for example controller → repository) | Route through the service layer, or adjust the rule if intended |
| Hub-like / god component | High Ca and Ce, or size far above the median | Split by responsibility (clusters inside the component) |
| Unstable dependency | Depends on a more unstable component | Invert, or stabilize the dependency |
| Shared persistence | Several components write the same entity | Single owner per entity; access through an API |
| Chatty interface | Many calls between two components per use case (static call count; confirmed by runtime) | Coarser API, batching |
| Distributed-monolith signals | Synchronous call chains across services, shared libraries with domain logic, shared database | Asynchronous boundaries, owned data |
| Dead or isolated code | No incoming references (entry points excluded: controllers, jobs, triggers) | Remove after confirmation |
| Framework (SAP Commerce) | Extension cycles; very wide `requires`; logic in interceptors or Jalo; one cron job doing everything | Extension split, service layer, job batching |
| Framework (Salesforce) | Multiple triggers per object; logic in triggers; SOQL/DML in loops; no selector layer; sharing inconsistencies | Trigger framework, selectors, bulkification |

### Performance (structural = potential; runtime = confirmed)

| Pattern | Static signal | Runtime confirmation |
|---|---|---|
| N+1 queries | Database or ORM access inside a loop (JPA lazy collections, JDBC, SOQL, FlexibleSearch, repository calls) | Repeated similar spans in one trace (Sentry, perf-sentinel style) |
| Unbounded results | Queries without limit or pagination; `findAll` on large entities | Large row counts and latency |
| Remote calls in loops / missing batching | HTTP or RPC client calls inside loops | Fan-out spans |
| Blocking in async code | Blocking I/O inside reactive or async handlers | Thread-pool saturation |
| Missing caching of expensive repeats | Same pure call with constant arguments in hot paths | Repeated identical spans |
| Expensive work in loops | Regex compile, string concatenation, reflection, object mapping in loops | CPU profiles |
| Inefficient data structures | List `contains` in loops; nested loops over collections | CPU profiles |
| Front-end weight | Large dependencies in `package.json`; no code splitting | Bundle analysis |
| Platform limits | Apex CPU, heap, SOQL and DML limits (ApexGuru-style); SAP queries per page | Org or server telemetry |

### Scalability and resilience

| Concern | Static signal |
|---|---|
| Stateful instances | In-memory sessions or caches without an external store; local file writes; static mutable state |
| Cloud blockers | Hard-coded hosts, IPs and paths; OS-specific calls; local schedulers (CAST Highlight-style blockers) |
| Missing timeouts, retries, circuit breakers | HTTP or database clients without timeouts; no resilience library around remote calls |
| Non-idempotent consumers | Message handlers without deduplication keys |
| Jobs on every node | Schedulers without leader election (SAP node groups) |
| Configuration in code | Secrets, endpoints and environment names in code (12-factor) |
| Database as integration point | Several services or components sharing tables |

## Recommendations

```
recommendation:
  id, title (plain), category (maintainability | performance | scalability | resilience | security)
  evidence: [graph facts, metrics, commits, spans, findings] with links
  evidence_class: structural | behavioural | runtime | ai_hypothesis
  affected: components, files
  expected_effect: direction and what is measured (e.g. "removes cycle A↔B; D(A) from 0.7 to ~0.3")
  effort: S | M | L (range with rationale)
  confidence: low | medium | high (never from the model alone)
  remediation: recipe id | AI change plan | manual guidance
  status: open | accepted | in progress | done (verified by re-analysis) | dismissed (reason)
```

Priority = expected effect × hotspot weight (behavioural evidence) ÷ effort. With no Git history,
it is structural only, and the page says so.

## AI architect (grounded)

- **Tools, not raw code.** Read-only tools over the model: components, metrics, cycles, rule
  violations, data-access map, hotspots, findings, plus excerpts on request. These mirror P03
  tools; MCP exposure is a later option.
- **Output.** An architecture review with sections (overview, strengths, risks, prioritized
  recommendations, trade-offs, open questions) and draft ADRs.
- **Verification.** Every claim cites a fact id or a code anchor. P03 checks rejected or uncited
  claims. Numbers come only from tools.
- **Control.** Off unless the project's AI policy allows it (P03).

## Remediation (P11)

1. **Deterministic refactorings first.** Move class, extract interface, introduce a facade, split
   a package, add a timeout configuration, batch queries. Use the OpenRewrite Apache-2.0 core for
   Java where suitable, and ts-morph/jscodeshift for TypeScript; check each license at adoption.
2. **AI multi-file change plans** in the fix workspace (P08), labelled AI and limited in scope.
3. **Verification.**
   - Re-analysis must show the smell gone and nothing new.
   - Architecture rules must pass.
   - Compile or build must pass where available (P09).
   - Metrics must move as expected.
4. **"What-if" simulation** before editing: recompute metrics on a virtual move.
5. **Migration plans** for decomposition (strangler steps, data ownership moves) as ordered
   backlog items with checks per step.

## Evaluation (required before strong claims)

- **Labelled dataset.** Synthetic fixtures with seeded smells (each catalog row positive and
  negative), plus public open-source projects with expert-labelled architecture issues (license
  permitting).
- **Measure:**
  - precision and recall per smell;
  - metric correctness against hand-computed values;
  - recommendation acceptance by reviewers;
  - for performance, static-flag precision against runtime-confirmed sets.
- **Report.** Measured numbers and dataset limits in the phase reports. No unmeasured accuracy
  claims.

## Implementation status

- **Slice 1 (9 October 2026, ADR 0018):**
  - components from Java packages and folders;
  - Martin metrics with evidence-gated zones;
  - cycles with the cheapest cut (exact up to 16 edges, Eades–Lin–Smyth beyond);
  - `GET /v1/snapshots/{id}/architecture`;
  - Structure health UI with a dependency matrix.
- **Slice 2 (9 October 2026, ADR 0019):** intended architecture as code (layers, forbid rules,
  expiring exceptions; versioned YAML); breaches as tracked issues with per-rule rechecks.
- **Slice 3 (9 October 2026, ADR 0020):** structural smells — cyclic dependency, unstable
  dependency, hub-like part — one issue per part (potential), generated code left out, LWC `c/`
  imports resolved; labelled evaluation set (`crp-dev arch-eval`). God component, dead code,
  shared persistence and chatty interfaces are not yet detected.
- **Computed on request** from the graph; the tables below are still the plan for persisted models
  and trends.

## Data model sketch (to be fixed in P10's migration)

- `architecture_models` (per snapshot): inferred components and layers, version.
- `architecture_rules` (per project): declared layers, allowed and forbidden dependencies;
  versioned and audited. **Implemented** as `architecture_rule_versions` (ADR 0019): append-only
  canonical documents; breaches are findings of the `architecture` engine rather than a separate
  table, so they share the issue lifecycle, triage and exports.
- `architecture_metrics`: per component and per snapshot.
- `architecture_findings`: smells, violations, performance and scalability signals, joined to
  issue lifecycle. **Implemented** for smells and rule breaches as findings of the `smells` and
  `architecture` engines (ADR 0019, ADR 0020); part-level findings carry `anchor_key`.
- `behaviour_stats`: from commits (requires history).
- `runtime_imports`: trace or APM files, mapped spans.
- `recommendations`: with status, evidence and remediation links.
