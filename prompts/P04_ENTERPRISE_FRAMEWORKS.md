# Execute Phase 4 — SAP Commerce and Salesforce packs

Read FRAMEWORK_ADAPTERS, standards/adoption policies and existing graph/engine contracts. Implement each pack in small tested rule/mapping families; preserve general language support.

## SAP Commerce

Detect supported platform/build versions conservatively. Map extension dependencies, Spring wiring, items/bean definitions, ImpEx/property references, OCC/service/facade/DAO relations, converters/populators, interceptors and scheduled/process/integration entry points where present.

Start with a bounded reviewed rule set: query parameterization and unbounded materialization candidates; repeated DAO/model access; risky interceptor side effects; cronjob batching/idempotency candidates; environment constants; sensitive logging; version-specific deprecated APIs; configuration dependency cycles. Each rule needs positive/negative examples and context-specific limitations. Do not execute ImpEx or delete generated/wired classes.

## Salesforce

Map project/packages, Apex/triggers, LWC-to-Apex, object/field references, Flows and supplied permissions/sharing/custom metadata. Integrate supported Salesforce Code Analyzer engines with exact-version configuration and deduplicate embedded engine overlap.

Start with bulk/loop operations, CRUD/FLS vs sharing, query injection, async/callout patterns, metadata/API compatibility and test-isolation candidates. Match advice to supported API versions and intended execution context; do not blindly insert permission checks that change business behavior.

## Validation and product

Display version/capability coverage per pack. Add source-backed graph views and actionable recommendations. Define conditional SAP build and Salesforce org validation profiles using customer-authorized environments; never redistribute proprietary SDKs or claim local Apex parsing proves deployability.

For every enabled rule and relationship, run domain-specific positive/negative, malformed/missing metadata and unsupported-version fixtures. Test secure XML parsing, generated code, dynamic configuration and overlapping analyzer results. Record SME-reviewed versus experimental status.

## Completion

Publish P04_REPORT.md with explicit supported versions, implemented rule IDs/mappings and limitations. Mandatory offline domain coverage must pass. Platform checks may be conditional and visibly unavailable; no runtime verification claim without actual environment evidence. Update capability and standards registries rather than advertising generic complete Hybris/Salesforce understanding.

