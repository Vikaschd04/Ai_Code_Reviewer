# ADR 0013 — Framework packs for SAP Commerce and Salesforce

Status: accepted. Date: 30 September 2026. Owner: repository owner (Phase 4 scope in
prompts/P04_ENTERPRISE_FRAMEWORKS.md); implemented by the development agent.

Context and constraints:

- Customers run SAP Commerce (Hybris) and Salesforce code. Much of their structure lives in
  configuration (Spring XML, items.xml, ImpEx, extension descriptors, Salesforce metadata), so a
  syntax-only graph misses the wiring and generic rules miss platform defects.
- Uploads are untrusted: configuration must be read as data (no DTDs, entities, ImpEx execution
  or deployments) and may be malformed or incomplete.
- No SAP distribution or Salesforce org is available; the platform may not redistribute
  proprietary SDKs. Source-level results must not be presented as build or runtime validation.
- The free hosting profile has 512 MB; packs must not add heavy runtimes.

Decision:

1. **Packs are adapters with capability records** (`crp_analysis/frameworks/`): detection from
   paths, conservative version detection with evidence (`manifest.json` `commerceSuiteVersion` /
   `build.number`; `sfdx-project.json` `sourceApiVersion` or metadata `apiVersion`), a
   per-capability coverage record (available / partial / unavailable with a reason), and an
   `experimental` status until a domain expert reviews them. Unknown or unvalidated versions are
   disclosed, never assumed.
2. **Secure, line-aware metadata reading** (`xmlsafe`): expat with DOCTYPE, entity and external
   reference handlers that refuse the document; namespaces reduced to local names; element counts
   bounded. Refused or malformed files are reported as partial coverage and FAILED file coverage.
3. **Mappings join the snapshot graph**: SAP extensions and Salesforce package directories are
   graph modules (`sap`, `sfdx` ecosystems) so dependency maps work unchanged; framework
   components (Spring beans/aliases, item/enum types, SObjects, fields, Apex classes/triggers,
   LWC, Flows, permission sets, custom metadata records) are nodes of kind `component`
   (migration `0006`); relations carry file/line evidence, the pack's extractor id and the usual
   classification. Targets outside the upload are `declared` (platform/standard) or `unresolved`
   (custom without metadata); nothing is guessed from names.
4. **Rules run on existing, pinned engines**: SAP Java patterns as owned Opengrep rules; Apex
   rules through the same pinned PMD 7.27.0 with a second trusted ruleset (engine `pmd-apex`,
   `crp-pmd-apex-v1`), which is the engine Salesforce Code Analyzer embeds, so no rule runs twice;
   cross-file configuration checks (extension dependency cycles, retired API versions) in a small
   in-process engine (`frameworks`). Every rule is catalogued with a sourced reference and has
   positive and negative fixtures.
5. **Presentation follows the reviewer-first rules**: platform checks appear only when they had
   files to check; a "Platform support" panel shows version status, coverage and what is not
   covered (build/org validation) with technical details collapsed.

Alternatives considered:

- *Salesforce Code Analyzer CLI*: its PMD engine duplicates the pinned PMD; its other engines
  (Flow Scanner, Salesforce Graph Engine, RetireJS, regex) need the Salesforce CLI plugin, extra
  runtimes and a separate evaluation. Deferred; recorded as a gap.
- *`@lwc/eslint-plugin-lwc`*: LWC JavaScript already gets the generic trusted ESLint rules;
  adding LWC-specific rules needs a pinned plugin evaluation. Deferred.
- *Executing ImpEx or builds to learn the model*: rejected (untrusted input, proprietary SDKs).
- *Heuristic guessing of beans/types by naming conventions*: rejected; configuration-driven links
  need file/line evidence.

Consequences and migration/reversal approach:

- Migration `0006` widens the node-kind check; downgrade deletes component nodes (their edges
  cascade). Graph builds of earlier scans are unaffected.
- The catalog moves to `crp-rules-v3`, invalidating per-file engine caches once; fingerprints and
  issue continuity are unchanged.
- Two engine runs per scan are added (`pmd-apex`, `frameworks`); both are `NOT_APPLICABLE` and
  hidden for other code.

Evidence and source/version references:

- PMD 7.27.0 Apex rules (`pmd-apex-7.27.0.jar`), Opengrep 1.30.0; SAP Help Portal pages on
  interceptors, the Jalo layer and `AbstractJobPerformable`; Salesforce Help 000389618 (API
  21.0-30.0 retirement); CWE-89, 532, 770, 1047, 1050. Report: docs/validation/P04_REPORT.md.

Affected contracts, phases and tests:

- API: `GraphSummary.frameworks`, node kind `component`, module dependency relation `config`,
  capabilities `sap_commerce_pack` and `salesforce_pack`; engines `pmd-apex` and `frameworks`.
- Tests: `packages/analysis/tests/test_frameworks.py`, `test_engines.py`,
  `test_security_engines.py`, `services/worker/tests/test_p04_frameworks.py`,
  `apps/web/e2e/p04-frameworks.spec.ts`.
