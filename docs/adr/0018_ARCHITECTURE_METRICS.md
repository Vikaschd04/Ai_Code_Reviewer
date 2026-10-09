# ADR 0018 — Architecture model and structural metrics (P10 slice 1)

Status: accepted; implemented and verified (hand-computed fixtures, randomized cut validity, a
real-stack review). Date: 9 October 2026. Owner: repository owner (prompts/P10_ARCHITECTURE_INTELLIGENCE.md);
implemented by the development agent.

Context:

- P10 must explain and measure a codebase's architecture with evidence, never guesses. Missing
  evidence must read as "not measurable", never zero.
- The P02 graph already holds files, types and resolved or inferred dependency edges per upload.
- Results must be correct against hand-computed values and must work at realistic sizes,
  including the 512 MB free profile.

Decision:

1. **Components.**
   - A Java file belongs to its package, taken from the fully qualified names of the types it
     declares. Every other file belongs to its folder.
   - Test code (the scope policy's `test` category) is left out and counted.
   - Team-edited components and layers come with intended-architecture rules (slice 2).
2. **Dependencies.**
   - Only resolved and inferred graph edges count, and only between different components. Edges
     to modules, external packages or unresolved targets are counted separately.
   - A component dependency's weight is its number of distinct file-level dependencies.
   - A Java on-demand import (`import a.b.*`) counts as a dependency on that package.
3. **Metrics** (R. C. Martin, files standing in for classes):
   - Ca = files outside a component that depend on it;
   - Ce = files inside it that depend on others;
   - I = Ce / (Ca + Ce);
   - A = abstract types / types. The graph extractor v2 marks interfaces, annotation types and
     abstract classes;
   - D = |A + I − 1|.

   Each ratio is undefined (null) when its denominator is zero. Abstractness is undefined for
   graphs built by extractor v1, which did not record abstract types.
   - D ≥ 0.7 is the zone of pain or of uselessness, reported only with evidence: pain needs at
     least 3 dependent files (a leaf used by one file is not hard to change), uselessness at least
     2 abstract types.
   - Fan-in and fan-out (distinct components) and size (files, lines, types) are also reported.
4. **Cycles.**
   - Tarjan's strongly connected components (iterative) on the component graph.
   - The cut is the cheapest set of component dependencies whose removal makes each cycle
     acyclic, weighted by file-level dependencies.
     - Exact search up to 16 component dependencies per cycle.
     - Above that, the weighted Eades–Lin–Smyth ordering, then a pass that puts back cut edges
       that close no cycle (when the cycle has at most 4,000 edges). These cuts are marked
       approximate.
   - A randomized test checks that every cut breaks its cycle and that the heuristic never
     reports a cut cheaper than the exact one.
5. **Serving.**
   - `GET /v1/snapshots/{id}/architecture` computes from the current graph build on request; the
     computation is pure and off the event loop.
   - Lists are capped: 400 component edges, and 50 members, edges and cut items per cycle, with
     full counts.
   - No new tables; persisted models, history and trends come with later slices.
6. **UI.**
   - "Structure health" on the Architecture tab: plain tiles (parts, cycles, hard to change,
     unused abstractions), cycles with what to cut, and "needs attention".
   - Collapsed: all measurements with definitions, and a 12-part dependency matrix with cycle
     cells marked.

Measured (pure computation, macOS arm64, random graphs, which are worst-case tangles):

| Files | Packages | Dependencies | Time | Peak memory |
|---|---|---|---|---|
| 1,000 | 50 | 8,000 | 0.2 s | 3 MB |
| 10,000 | 400 | 80,000 | 1.0 s | 34 MB |
| 50,000 | 2,000 | 400,000 | 7.4 s | 193 MB |

The first implementation used a repeated greedy cut, which is O(E²). It took 7 s for the
50-package case and would not have finished on the larger ones; it was replaced before release.

Consequences:

- Reviewers see cycles with concrete imports to remove, and parts that are hard to change. Each
  claim traces back to the graph's evidence.
- Packages and folders are a structural approximation. Layer- and domain-level components need
  the team's intended architecture (slice 2) and framework-aware grouping (SAP extensions,
  Salesforce packages), which are planned.
- Measuring on request costs a few seconds for very large graphs. Caching per build is a later
  optimisation.

Alternatives considered:

- Class-level graphs, rather than file-level: tree-sitter syntax gives types, but dependencies
  are resolved per file. Files are the honest unit today.
- Reporting Ca and Ce as component counts, as many tools do: kept as fan-in and fan-out, while Ca
  and Ce follow Martin's class-level definition.
- Exact minimum feedback arc set for all sizes: NP-hard, so it is exact only for small cycles.
