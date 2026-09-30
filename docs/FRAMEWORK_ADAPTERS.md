# Framework adapters

Status: target Phase 4 scope. Earlier phases can identify framework indicators while declaring deeper support unavailable.

## Common capability record

Adapter ID/version; compatible platform ranges; detection evidence; file formats; mapping/rule capabilities; parser/engine prerequisites; generated-source policy; build/runtime prerequisites; fixture suites; license inventory; unsupported scenarios. Unknown platform versions use conservative rules and disclose uncertainty.

## SAP Commerce

Parse localextensions.xml, extensioninfo.xml, Spring XML/annotations, items.xml/beans, ImpEx, properties, build descriptors, OCC, services/facades/DAOs, strategies/populators/converters, interceptors, cronjobs, process definitions and integrations as implemented. Use secure XML parsing and explicit ImpEx support boundaries; an import parser does not execute data changes.

Map extension dependency, Spring injection, controller-to-facade/service, DAO/model access, converter/populator composition and job/process relations. Configuration-driven links need file/span evidence, not guessed Java imports. Generated model sources are resolvable context, not default edit targets.

Rules begin with validated candidates for query parameterization/bounds, repeated access, job batch/idempotency behavior, transaction/interceptor side effects, catalog/session/search-restriction context, logging/configuration and version-specific deprecation. Do not label every loop query or offset page universally incorrect; validate usage and workload assumptions.

## Salesforce

Parse sfdx-project.json, package structure, Apex/triggers, LWC, optional Aura/Visualforce, objects/fields, Flow definitions, permission/sharing/custom metadata and integrations. Link LWC calls to Apex, query/DML to objects/fields, and triggers/Flows to affected objects where metadata exists.

Use Salesforce Code Analyzer with pinned per-engine configuration, avoiding duplicate PMD/ESLint results. Review bulkification, limits-sensitive paths, CRUD/FLS vs sharing, injection, async/callout handling, API compatibility and test isolation. Missing org metadata means partial mapping.

## Validation environments

SAP build checks require the correct licensed distribution/dependencies; Salesforce Apex/deployment checks require an appropriate authorized org. No SDK redistribution or production-org tests by default. Adapters state the actual environment and checks performed. Source-only tests can pass while a deployment remains unverified.

## Adapter gate

For each first supported version family: at least one mapping fixture, positive/negative examples for every enabled rule, missing-dependency and unsupported-version cases, malformed-config protection, evidence anchoring and no modification of input. Domain SME review is required before strong production-support claims; it is not a blocker for building clearly experimental capability.


## Implemented in P04 (experimental, not SME-reviewed)

Packs live in `packages/analysis/src/crp_analysis/frameworks/` (ADR 0013). Each graph build stores
one `PackReport` per detected platform (`GraphSummary.frameworks`): version, version status
(`supported` / `unsupported_version` / `unknown_version`) with path:line evidence, capability
states with reasons, relation and component counts, rule IDs and notes. Supported versions,
mappings and rule IDs: docs/validation/P04_REPORT.md.

- SAP Commerce `crp-pack-sap-commerce-v1`: extensions, `localextensions.xml`, Spring XML and
  `@Resource`/`@Qualifier` injection, items.xml types/relations/enums, interceptor mappings,
  ImpEx headers and `ServicelayerJob` `springId`; 8 Opengrep rules and the extension-cycle check.
- Salesforce `crp-pack-salesforce-v1`: package directories, Apex classes/triggers, static SOQL
  objects, LWC to Apex/schema, objects/fields/lookups/sharing model, Flows, permission sets,
  custom metadata; 11 PMD Apex rules (`crp-pmd-apex-v1`) and the retired-API-version check.
- Metadata is read with `frameworks/xmlsafe.py` (DOCTYPE/entities refused, line numbers kept).

## Validation profiles (conditional; not run)

These need a customer-authorized environment and are shown as unavailable until one is
configured. No SDK or org credentials are stored or redistributed by the platform.

| Profile | Prerequisites | Would run | Would record |
|---|---|---|---|
| SAP build | Customer-licensed SAP Commerce distribution matching the declared version, the customer's `config/` and build JDK, in an isolated worker without production credentials | `ant clean all` and, when requested, `ant unittests -Dtestclasses.packages=<project packages>` on a copy of the snapshot | Tool versions, exit codes, compiler and test reports; failures are findings with evidence, never hidden |
| Salesforce org | An authorized sandbox or scratch org and a short-lived token supplied for the run; never a production org by default | `sf project deploy validate` (check-only) and `sf apex run test` in that org | CLI version, deploy/test results and coverage; the org alias, never the token |

Until a profile has run, source-level results say "not built" / "not deployed" and the pack
capability `build_validation` / `org_validation` stays `unavailable`.
