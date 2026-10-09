# ADR 0020 — Structural architecture smells (P10 slice 3)

Status: accepted; implemented and verified (hand-computed unit fixtures, a labelled evaluation
set, real-stack reviews, scale measurements). Date: 9 October 2026. Owner: repository owner
(prompts/P10_ARCHITECTURE_INTELLIGENCE.md, deliverable 4); implemented by the development agent.

Context and constraints:

- P10 deliverable 4 asks for catalogs of structural smells with evidence classes; structural
  signals are "potential". Each catalog row needs positive and negative fixtures (Java,
  TypeScript, SAP Commerce, Salesforce), sourced rationale, handling of generated and test code,
  and measured precision and recall on a labelled set.
- Smells must become tracked findings without ever reporting a change of anchor or of unrelated
  code as a fix.
- The original papers' exact thresholds could not be verified (one paper was not reachable, the
  other does not state them). Arcan's published definitions were verified on its documentation.

Decision:

1. **Catalog** (`crp_analysis/architecture/smells.py`, engine `smells`, catalog `crp-rules-v4`):
   - `crp.arch.cycle` — parts in a strongly connected group of the part graph (R. C. Martin's
     Acyclic Dependencies Principle; Arcan "Cyclic Dependency"), with the cheapest cut of
     ADR 0018. Severity medium.
   - `crp.arch.unstable-dependency` — part A uses parts whose instability exceeds A's by more
     than 0.1, and these are at least 30% of the parts A uses (Martin's Stable Dependencies
     Principle; Arcan "Unstable Dependency"). Dependencies inside a cycle are left to the cycle
     smell. Severity low.
   - `crp.arch.hub` — fan-in and fan-out (distinct parts) both at least the system's upper
     quartile and at least 3, in a system of at least 8 parts (Arcan "Hub-Like Dependency").
     Severity medium.
   - The thresholds are refactorX's, chosen to need clear evidence; they are not claimed to
     equal Arcan's, which combine the system with a benchmark of more than 100 systems.
   - Not in this slice: god component (needs a size baseline), dead code (syntax-level
     resolution would give many false positives with dependency injection and reflection),
     shared persistence and chatty interfaces (need the data-access and call maps).
2. **One finding per part.** Each smell is reported once per part, at the part's first file
   with the evidence (by path) and that file's first such line; the message lists the part's
   other files. A tangled codebase yields one issue per part, not per file. A hub is anchored at
   the part's first file.
3. **Issues follow their part.** Smell findings carry `details.anchor_key` (for example
   `hub|com.acme.hub`). When the anchor file changes but the part still has the smell, the issue
   moves to the new file (event `moved`, "the part's anchor file changed") instead of being
   verified absent and recreated. Comparisons (fix workspace checks, Git reviews, scan
   comparisons) pair findings by the same key. A smell that disappears is verified absent only
   at its anchor file, under the same engine, rules and extractor version.
4. **Generated and test code.** The architecture model leaves out test code (scope policy) and
   generated code: path segments `gensrc` (SAP Commerce), `generated`, `generated-sources`,
   `generated-test-sources`, `__generated__`, and `*.generated.*` names. Structure health and the
   architecture rules use the same model and report the counts.
5. **Salesforce LWC.** The dependency map now resolves Lightning Web Components' `c/<name>`
   imports to the sibling bundle `lwc/<name>/<name>.js` or `.ts` (resolved), or to a single
   bundle of that name elsewhere (inferred). Graph extractor v3. Architecture engines record the
   extractor in their version, so absences across extractor versions are never verified fixes.
6. **Engine.** `smells` runs after the graph step (like the architecture rules) on the shared
   model; it is always planned when there are source files. Coverage is honest: files the map
   could not read are NOT_ATTEMPTED. Diagnostics hold the thresholds and the parts affected per
   smell. Findings may carry a specific title (`RawFinding.title`); catalog text supplies the
   explanation, recommendation and source link.
7. **Evaluation.** `fixtures/architecture-eval` holds 10 hand-labelled cases. Each smell has
   positive and negative cases, across Java, TypeScript, an SAP Commerce extension with `gensrc`
   and Salesforce LWC. `crp-dev arch-eval` analyzes each with the real extraction and reports
   precision and recall per smell.

Alternatives considered:

- One finding per participating file: precise per file but 50,108 findings on the
  50,000-file worst-case tangle; per part gives 2,128.
- Only the cut's files: the cheapest cut can change without the code changing, which would make
  findings disappear without a fix.
- Path-independent fingerprints for part-level findings: would weaken the "absence is verified
  at a file" rule that every other engine follows; anchor moves keep it.

Consequences and migration/reversal approach:

- Every review gains the `smells` engine; projects with tangled packages see new maintainability
  issues labelled potential. Extractor v3 re-extracts every file once (per-file cache keys
  change) and the catalog move to v4 invalidates per-file engine caches once.
- Smells are structural; they do not measure change cost (history) or runtime impact. Hotspots
  from Git history (slice 4) and runtime evidence (slice 5) are what can rank them.

Evidence and source/version references: [Arcan documentation — architectural
smells](https://docs.arcan.tech/2.8.0/architectural_smells/) (definitions);
Arcelli Fontana, Pigazzini, Roveda and Zanoni, "Automatic Detection of Instability Architectural
Smells", ICSME 2016; R. C. Martin, "Agile Software Development" (2002), ch. 20;
docs/validation/P10_REPORT.md (slice 3).

Affected contracts, phases and tests: engine `smells` in scan engine lists; architecture summary
`generated_files`; `test_architecture_smells.py`, `test_p10_architecture.py`
(`test_architecture_smells_on_real_reviews`), `fixtures/architecture-eval`.
