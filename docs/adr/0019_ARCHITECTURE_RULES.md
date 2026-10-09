# ADR 0019 — Intended architecture as code (P10 slice 2)

Status: accepted; implemented and verified (unit fixtures with hand-computed breaches, API tests,
real-stack reviews on the lite runner and on Temporal, a browser journey). Date: 9 October 2026.
Owner: repository owner (prompts/P10_ARCHITECTURE_INTELLIGENCE.md, deliverable 3); implemented by
the development agent.

Context and constraints:

- P10 asks teams to declare layers and allowed or forbidden dependencies, versioned and audited,
  with YAML import and export. Every review must report deviations as findings with the issue
  lifecycle.
- Mandatory tests: breaches detected and lifecycle-tracked; exceptions with expiry; a rule change
  re-evaluates honestly (a rule edit is never mistaken for a fix).
- The model must be the one Structure health uses (ADR 0018): Java packages and folders, test
  code left out, file-level dependencies from the graph.
- Imported YAML is untrusted input.

Decision:

1. **Rules document** `crp-architecture-rules-v1` (`crp_analysis/architecture/rules.py`).
   - `layers`, top to bottom, each a list of patterns over part keys. Dots and slashes separate
     name parts; `*` matches within one part and `**` any number of parts, including none. A part
     belongs to the first layer that matches it; overlaps are reported.
   - `layering`: `lower` (default; a layer may use any layer below it), `next` (only the layer
     directly below it) or `none`. With fewer than two layers there is no layering.
   - `forbid`: `from` must not use `to`, each side a layer name (case-insensitive) or a pattern,
     with a stable `key` (derived from `from` and `to` when omitted), a reason and a severity.
   - `allow`: exceptions to layering only, each with a required reason and an optional `until`
     date. An expired exception stops applying and is listed.
   - Precedence: a forbid rule always reports; otherwise layering applies between two different
     layers unless an unexpired exception allows the use. Parts in no layer are checked only by
     forbid rules; dependencies inside one part are never checked.
   - Validation collects every problem with its location (for example `layers[1].match[1]: empty
     name part`). Limits: 30 layers, 20 patterns per layer, 100 forbid and 100 allow rules.
2. **YAML as data.** PyYAML 6.0.3 `SafeLoader` (MIT; TOOLCHAIN.md). Anchors and aliases are
   refused before construction (no expansion attacks), tags beyond the safe set fail, one
   document only, 64 KB at most. Export writes the canonical document with a commented header,
   so an export imports unchanged with the same hash.
3. **Versions** (`architecture_rule_versions`, migration 0012). Append-only per project: version
   number, canonical document, SHA-256, source (`editor` or `yaml`), note, author and time. The
   history is the audit trail. Saving needs the MEMBER role and the version the editor loaded
   (`base_version`; a newer one is a 409). Identical rules add no version.
4. **Engine `architecture`** (`crp_analysis/engines/architecture.py`), a finding engine.
   - Planned at review start with the project's newest version (recorded in the run's
     diagnostics, `enabled_rules` and `ruleset_sha256`). Without rules it is NOT_APPLICABLE with
     the reason "No architecture rules are set for this project."
   - Runs after the graph step of the same review (`AFTER_GRAPH`): Temporal runs it in a second
     phase; the lite runner runs engines in order. Histories recorded before this change never
     planned it, so they replay unchanged.
   - Reads the snapshot's current, non-failed dependency map of the same extractor through the
     shared reader (`crp_core/db/graph_reads.py`) used by Structure health. Without a usable map
     it FAILS with every file NOT_ATTEMPTED; files the map could not read are NOT_ATTEMPTED.
   - One finding per source file and target, anchored at the first evidence line (usually the
     import); severity from the rule, category maintainability, guidance from the rule and its
     reason. `details` carries the rule's own hash, the rules version, parts and layers.
   - Engine version `1.0.0+graph.<extractor hash>`: a new extractor can find other dependencies,
     so absences across extractor versions are not verified fixes.
5. **Honest recheck with per-rule hashes.** Each rule has a hash of what decides its detection
   (the layering rule: layers, layering and exceptions; a forbid rule: its sides, plus the layers
   when a side names one). Reasons and severities are excluded. The run reports
   `diagnostics.rule_hashes`; an observation records its rule's hash. `classify_absence` compares
   the rule's own hash when a run reports them:
   - same hash, file checked → VERIFIED_ABSENT (fixed);
   - rule changed → UNKNOWN ("rule … changed since the earlier observation");
   - rule removed → RULE_OBSOLETE.

   Editing one rule does not make other rules' absences unverifiable. The same logic serves the
   issue lifecycle, change-set and Git review comparisons and scan comparisons.
6. **API** (tag `architecture`): `GET/PUT /v1/projects/{id}/architecture-rules` (newest or
   `?version=`, with history), `GET …/architecture-rules/export` (YAML), and
   `POST /v1/snapshots/{id}/architecture-rules/check`, a read-only evaluation of given or saved
   rules on an upload (no findings written; lists capped).
7. **UI.** "Architecture rules" on the project's Architecture tab: a plain summary (layers top to
   bottom, counts, version, author, note, breaches in the latest upload), an editor with "Check on
   the latest upload" before saving, validation problems listed, YAML download, a collapsed
   history. The Issues tab gains a check filter (`?check=architecture`), and its counts follow it.

Alternatives considered:

- ArchUnit or jQAssistant: these need compiled classes or a running Neo4j and execute or load
  the project. The source-only, no-execution rule rules them out for now.
- A whole-set hash only: any rule edit would make every absent breach UNKNOWN forever. Per-rule
  hashes keep rechecks verifiable without ever counting a rule edit as a fix.
- Catalog entries per user rule: the catalog is static. User rules carry their own guidance,
  which the platform already supports for findings outside the catalog.
- Rules stored in the repository (`.refactorx/architecture.yaml`) and read from uploads: uploads
  are untrusted and their instruction files are never followed. Teams can keep the exported YAML
  in their repository and import it; reading it automatically is a possible later step behind an
  explicit project setting.

Consequences and migration/reversal approach:

- One new table (migration 0012, reversible); the new engine is NOT_APPLICABLE for projects
  without rules, so existing reviews only gain a "not applicable" row.
- Rule severity is the team's choice and is labelled as such in the finding.
- Parts are packages and folders. Framework units (SAP extensions, Salesforce packages) as parts
  come later with framework-aware grouping.
- Breaches inherit the graph's resolution gaps (K-P02-03): an unresolved import cannot be judged.

Evidence and source/version references: PyYAML 6.0.3 on PyPI (MIT; 25 Sep 2025), types-PyYAML
6.0.12.20260906 (Apache-2.0); docs/validation/P10_REPORT.md (slice 2).

Affected contracts, phases and tests: `packages/contracts/openapi.json` (architecture tag, issue
counts scoped by check); `test_architecture_rules.py` (40), `test_architecture_rules_api.py` (3),
`test_p10_architecture.py` (real reviews on lite and Temporal), `e2e/p10-architecture-rules.spec.ts`.
