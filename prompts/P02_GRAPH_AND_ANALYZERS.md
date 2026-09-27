# Execute Phase 2 — Persistent mappings and broader analysis

Read the current phase evidence, ARCHITECTURE, DATA_MODEL, ANALYSIS_PIPELINE, ENGINE_ADOPTION and relevant UI/API specs. Keep ZIP/non-Git snapshots first-class throughout.

## Deliver

1. Store snapshot-versioned module/file/symbol/relationship mappings with precise source evidence, extractor identity and declared/resolved/inferred classifications.
2. Improve Java and TypeScript definition/type resolution where dependencies permit. Record missing classpaths/configuration and unresolved dynamic edges rather than guessing.
3. Add bounded graph/impact APIs and UI drill-down plus table equivalents. Use Postgres initially; show provenance and source navigation. Do not render entire huge graphs.
4. Adopt pinned Opengrep with owned/approved rules and Trivy after exact-version license/config/security review. Run actual integrations and expose unsupported formats or unavailable databases.
5. Normalize results across engines; preserve original reports/severity/provenance. Add deterministic duplicate correlation without collapsing distinct flows.
6. Add issue lifecycle, owner/exception reason/expiry, snapshot comparison and JSON/SARIF exports. An absent finding in a partial/incompatible scan is not resolved.
7. Introduce correct caching and invalidation keyed by content plus parser/rule/dependency/config scope. Periodic/full rescan remains possible.

## Mandatory checks

Graph anchors exist in the relevant snapshot. Failed reparsing cannot present stale links as current facts. Authorized users see only their project nodes/source/exports. Test partial analyzer coverage, duplicate families, different flows, configuration-only changes, rule-version changes, deletions/renames and bounded graph queries. Validate exports against actual schemas. Run real additional engines on positive/negative examples.

Benchmark one documented medium fixture and record time/memory without claiming enterprise scale. Retain API/UI responsiveness via background jobs and pagination. No AI inference should be promoted to an observed graph fact.

## Completion

Produce P02_REPORT.md, update schema/API docs, capabilities, engine inventory and memory. Existing Phase 1 workflows must still work without model credentials or Git integration. Explain remaining semantic-resolution gaps clearly.

