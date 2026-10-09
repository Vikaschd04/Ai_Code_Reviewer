# Phase P10 validation report — Architecture intelligence

Date: 9 October 2026. Environment: macOS arm64, Python 3.14, PostgreSQL 18.6, Temporal CLI dev
server, PMD 7.27.0, ESLint 10.11.0, Opengrep 1.30.0, Trivy 0.69.3. Decision record:
[ADR 0018](../adr/0018_ARCHITECTURE_METRICS.md).

Status: **IN_PROGRESS.** Slice 1 is delivered and verified: the architecture model and structural
metrics, with Structure health in the UI. Slices 2–8 are planned (docs/ROADMAP.md):

2. intended-architecture rules;
3. smell, performance and scalability catalogs;
4. Git-history hotspots;
5. runtime evidence;
6. recommendations;
7. grounded AI architect, which needs the owner's key;
8. evaluation.

## Slice 1 deliverables

| Deliverable (P10 prompt) | Delivered | Where |
|---|---|---|
| 1. Architecture model (components) | Partly delivered: components from Java packages and folders; test code left out and counted. Not yet: team-edited components and layers, framework-aware grouping, the data-access map, interfaces | `services/architecture.py`, `crp_analysis/architecture/metrics.py` |
| 2. Metrics | Partly delivered: Ca, Ce, I, A, D, fan-in and fan-out, size, cycles with the cheapest cut; undefined ratios stay null. Not yet: complexity, modularity, data sharing, behavioural metrics | same; `GET /v1/snapshots/{id}/architecture` |
| 8. UI | Partly delivered: Structure health (tiles, cycles with what to cut, needs attention, all measurements, dependency matrix). Not yet: C4-style views, hotspot treemap, trends | `components/ArchitectureHealth.tsx` |
| Graph extractor v2 | Abstract types marked for Java (abstract classes, interfaces, annotation types) and TypeScript (abstract classes, interfaces); older builds show abstractness as not measurable | `graph/extract.py`, `graph/resolve.py` |

## Mandatory tests that apply to slice 1

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Metrics match hand-computed values | Layered fixture `app → svc → dom` with abstract types, duplicates, a same-component dependency and an unresolved one; zone fixture; on-demand package imports; test code | PASS. app (0, 2, 1.0, 0.0, 0.0), svc (2, 1, 0.333, 0.5, 0.167), dom (2, 0, 0.0, 0.5, 0.5); edge weights 2, 2 and 1; pain and uselessness detected, a one-dependent leaf not flagged; ratios null without evidence | `test_architecture_metrics.py` (8) |
| Cycles found with the minimal edge set suggested | Two overlapping cycles where cutting the heavy edge (cost 3) loses to two light edges (cost 2); two separate cycles; 60 random graphs comparing exact and heuristic | PASS. Exact cut = {y→x, y→z}. Every heuristic cut breaks its cycle and is never cheaper than the exact one | same |
| Real review | Synthetic Java and TypeScript project uploaded and reviewed (real graph extraction and resolution) | PASS. The API returns the hand-computed values, the package cycle x↔y with a one-dependency exact cut, folder components for TypeScript, and the test file excluded | `test_p10_architecture.py` |
| Scale | Pure computation on random (worst-case) graphs | 1,000 files: 0.2 s and 3 MB. 10,000 files: 1.0 s and 34 MB. 50,000 files and 400,000 dependencies: 7.4 s and 193 MB. Responses capped (400 edges, 50 per cycle list) | ADR 0018 |
| Browser | Architecture tab on the graph fixture | PASS. Structure health shows parts, the measurements table (`com.acme.core`) and the dependency matrix; no horizontal scroll at 390 px | `e2e/p02-analysis.spec.ts`; `p10-structure-health.png`, `p10-structure-health-mobile.png` |
| No history = not available | — | Not in slice 1 (behavioural metrics are slice 4) | — |
| Rule violations, catalogs, runtime import, AI architect, evaluation | — | Not in slice 1 | — |

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint, format, types and contracts | `make check` | exit 0 |
| Types for Linux | `uv run mypy --platform linux` | no issues in 179 source files |
| Tests | `make test` | exit 0: 512 pytest (P10 added 9: 8 metric tests, 1 real-stack review) + 29 vitest |
| Browser E2E | `caffeinate -i make test-e2e` | exit 0: 19/19 in 5.3 minutes (the P02 architecture journey covers Structure health) |

## Findings during the slice

- **Performance:** the first cycle-cut implementation, a repeated greedy, was O(E²): 7 s for 50
  tangled packages and impractical beyond. Replaced with weighted Eades–Lin–Smyth plus a restore
  pass: 0.2 s, and half the cut size.
- **Honesty:** Martin's zone of pain flagged leaf utilities used by a single file. Zones now need
  evidence (pain: at least 3 dependent files; uselessness: at least 2 abstract types).
- **Name clash:** a new helper in the graph routes shadowed an existing one; mypy caught it before
  it reached the neighborhood endpoints.

## Limitations

- Components are packages and folders. Layers, domains and framework units (SAP extensions,
  Salesforce packages) come with slice 2 and the framework mapping.
- Dependencies come from the syntax-level graph (K-P02-03), so resolution gaps lower the counts.
  The number of edges not counted is shown.
- Measured on request, without caching or history.
