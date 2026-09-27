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

