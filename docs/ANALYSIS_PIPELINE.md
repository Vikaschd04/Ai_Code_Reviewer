# Analysis pipeline

## Stages and outputs

1. Intake freezes a snapshot and complete submitted/captured manifest.
2. Inventory detects languages, build manifests, versions, generated/vendor scope and unknowns without executing source.
3. Structural parsing extracts available modules/symbols/relations; errors are recorded per file.
4. Semantic/framework adapters resolve supported references and declare missing dependencies.
5. Applicable deterministic engines execute on approved scope in bounded environments.
6. Normalization validates paths/ranges, preserves raw evidence and correlates true duplicates.
7. Optional AI receives bounded evidence/context and returns schema-valid proposals.
8. Verification checks anchors/preconditions/counterexamples; report publishing exposes coverage and uncertainty.
9. Later repair workflows fork a copied snapshot and attach validation to a patch.

## Adapter interfaces

SourceAdapter: validate source reference, capture, manifest, cancel. ParserAdapter: detect, parse, resolve where supported, emit graph and diagnostics. EngineAdapter: capabilities, plan, run, normalize, coverage, cancel. FrameworkAdapter: detect version, map config, supply validated rules/validation profile. ModelAdapter: structured invocation, tool allowlist, usage, timeout, retry. Keep typed DTOs and contract tests independent of tool-specific JSON.

Engine inputs include snapshot_id, scope manifest, trusted config, pinned engine/rule identity and resource budget. Outputs include outcome, per-file/rule coverage, raw-artifact IDs, normalized findings, diagnostics and usage. An exit code is interpreted through that engine's documented contract; nonzero may mean findings, failure or both.

## Coverage accounting

Report discovered/submitted entries; accepted regular files; excluded/unsupported/unreadable files; eligible/attempted/successful/failed analyzer scopes; parsed and semantically resolved symbols; AI-reviewed units. These categories have different denominators and are not interchangeable. Document set relationships in code and test accounting invariants.

Do not claim per-rule/per-file execution precision the upstream tool cannot expose. Mark coverage estimated or coarse, using the invocation scope and successful tool completion as its evidence. A source location read for context is not automatically AI-reviewed.

## Graph and issue correctness

Store snapshot-scoped nodes/edges with extractor version and declared/resolved/inferred/runtime-observed classification. Type resolution may require builds; syntax matching alone cannot prove every caller. On failed reparse, invalidate or clearly mark stale graph entries. Cross-file resolution failures are visible in impact calculations.

Deduplicate by compatible rule family, symbol/evidence and location/flow, preserving detector provenance. Identical severity/title is insufficient. Repeated scans with changed rules or scope must not silently report issues fixed. A compatible successful recheck is required for verified absence.

## Caching and incremental work

Syntax cache: blob hash + parser/version/options. Semantic cache adds classpath/dependency/config fingerprint. Engine cache adds rulepack and scope. AI cache adds actual retrieved-source hashes, prompt/model/settings and policy. Always preserve tenant boundary and authorization. Config/lockfile/permission changes can invalidate untouched files. Full reconciliation remains available.

## Reports

Persist raw engine artifacts, schema-valid JSON/SARIF where supported, file coverage and human-readable summary. Include snapshot identity, optional Git metadata, tool/rule/model versions, durations, errors, unavailable checks and applied exclusions. No quality score without transparent underlying dimensions.

