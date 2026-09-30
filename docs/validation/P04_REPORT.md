# Phase P04 validation report — SAP Commerce and Salesforce packs

- Date: 30 September 2026. Actor: Claude Code development agent (Opus 5.5), single agent.
- Status: **COMPLETE for the mandatory offline domain coverage**; both packs are
  **experimental** (no SME review yet). Conditional platform checks (SAP build, Salesforce org)
  are visibly unavailable: no licensed SAP distribution or authorized org was used, so nothing
  here is a build, deployment or runtime claim.
- Design: [ADR 0013](../adr/0013_FRAMEWORK_PACKS.md). Environment as in P03_REPORT (macOS 26.5.2
  arm64, Python 3.14.3, Node 22.22.0, PostgreSQL 18.6, Temporal CLI 1.9.1); engines PMD 7.27.0
  (including `pmd-apex-7.27.0.jar`), Opengrep 1.30.0, ESLint 10.11.0 + typescript-eslint 8.70.1.
- Dataset: synthetic fixtures only — `fixtures/projects/sap-commerce-mixed` (5 extensions incl.
  a deliberate cycle and a malformed descriptor) and `fixtures/projects/salesforce-mixed` (SFDX
  project with Apex, trigger, LWC, objects, Flow, permission set, custom metadata and an XXE
  attempt).

## Supported versions

| Pack | Adapter | Supported (validated on fixtures) | Detection | Other versions |
|---|---|---|---|---|
| SAP Commerce | `crp-pack-sap-commerce-v1` | SAP Commerce Cloud 2105, 2205, 2211 | `manifest.json` `commerceSuiteVersion` or `build.number` `version=` (path:line evidence) | Older (e.g. 1905) → `unsupported_version`; none declared → `unknown_version`; mapping and rules still run and the UI says so |
| Salesforce | `crp-pack-salesforce-v1` | SFDX source format, API 31.0 and later | `sfdx-project.json` `sourceApiVersion`, else highest metadata `apiVersion` | 30.0 and earlier → `unsupported_version` (retired range 21.0–30.0, Salesforce Help 000389618) |

## Implemented mappings (graph relations with file/line evidence)

- **SAP Commerce**: extensions as modules (`requires-extension` → `depends_on`), `localextensions.xml` → `loads_extension`; Spring beans and aliases (`implemented_by` class, `injects` from property/constructor refs, `<ref bean>` lists and p-namespace `-ref`, `extends_bean` parents, `alias_of`); `@Resource(name=…)` / `@Qualifier("…")` annotation injection (OCC controllers → facades → services); item types and enums (`extends_type`, `relates_to`); `InterceptorMapping` → `intercepts` item type; ImpEx headers → `imports_data`, `ServicelayerJob` `springId` → `runs_bean`.
- **Salesforce**: package directories as modules (package dependencies → `depends_on`); Apex classes (sharing mode, test flag, entry points: future/callout/AuraEnabled/InvocableMethod/RestResource/Batchable/Queueable/Schedulable) and triggers (`triggers_on` with events); static SOQL `FROM` → `queries` (comments and strings masked first; dynamic queries counted, not guessed); LWC `@salesforce/apex` → `calls_apex`, `@salesforce/schema` → `references_schema`; objects/fields (`field_of`, `lookup_to`, org-wide sharing model); Flows (`flow_triggers_on`, `flow_uses_object`, `flow_calls_apex`); permission sets (`grants_object_access` with operations, `grants_class_access`); custom metadata records (`record_of`).
- Targets outside the upload are `declared` (platform extensions/beans/types, standard objects, packaged classes) or `unresolved` (custom objects without metadata); nothing is linked by naming convention alone.

## Implemented rules

| Rule ID | Engine | Pack | Focus | Positive / negative evidence |
|---|---|---|---|---|
| `crp.sap.flexiblesearch.string-concat` | opengrep | SAP | query parameterization | `DefaultShopProductDao.findByName` / `findByCode` (bound `?code`) |
| `crp.sap.flexiblesearch.unbounded-result` | opengrep | SAP | unbounded materialization (candidate) | `findAll`, `findByName` / `findByCode` (`setCount`) |
| `crp.sap.model.save-in-loop` | opengrep | SAP | repeated model access | `addPoints` / `resetPoints` (`saveAll`) |
| `crp.sap.interceptor.persisting-side-effect` | opengrep | SAP | interceptor side effects | `LoyaltyPrepareInterceptor` / `AuditValidateInterceptor` |
| `crp.sap.cronjob.missing-abort-check` | opengrep | SAP | cron job batching/abort (candidate) | `LoyaltyRecalculationJob` / `SafeCleanupJob` |
| `crp.sap.jalo.deprecated-api` | opengrep | SAP | version-specific legacy API | `LegacyPriceHelper` / generated `gensrc` Jalo class excluded |
| `crp.java.config.hardcoded-environment-url` | opengrep | SAP (Java) | environment constants | `EndpointConfig.loyaltyEndpoint` / `paymentEndpoint` |
| `crp.java.logging.sensitive-data` | opengrep | SAP (Java) | sensitive logging | `PaymentLogger.logAttempt` / `logResult` |
| `crp.sap.extension.dependency-cycle` | frameworks | SAP | configuration dependency cycles | `shopimport` ↔ `shopexport` / acyclic extensions |
| `OperationWithLimitsInLoop`, `OperationWithHighCostInLoop`, `AvoidNonRestrictiveQueries` | pmd-apex | Salesforce | bulk/loop, limits | `AccountService`, `ReportService` / `SafeAccountService`, `safeLabels` |
| `ApexCRUDViolation`, `ApexSharingViolations` | pmd-apex | Salesforce | CRUD/FLS vs sharing | `AccountService`, `LegacyIntegration` / user-mode `SafeAccountService` |
| `ApexSOQLInjection` | pmd-apex | Salesforce | query injection | `AccountService.getAccounts` / bind variables |
| `ApexSuggestUsingNamedCred`, `ApexInsecureEndpoint`, `QueueableWithoutFinalizer` | pmd-apex | Salesforce | callouts and async | `LegacyIntegration`, `LoyaltyQueueable` / `LoyaltyInvocable` |
| `ApexUnitTestShouldNotUseSeeAllDataTrue`, `ApexUnitTestClassShouldHaveAsserts` | pmd-apex | Salesforce | test isolation | `AccountServiceTest` / `SafeAccountServiceTest` |
| `crp.sf.metadata.retired-api-version` | frameworks | Salesforce | metadata/API compatibility | `LegacyIntegration.cls-meta.xml` 29.0 / 62.0 metadata |

Every rule is in `rules/catalog.json` (`crp-rules-v3`) with a sourced reference (PMD docs, SAP Help Portal, Salesforce Help, CWE, 12-factor). The CRUD/FLS guidance tells reviewers to decide the intended execution context first instead of adding checks that change business behaviour.

## Mandatory checks (P04 prompt)

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Positive/negative per enabled rule | Real Opengrep and PMD on the fixtures; exact rule/file/line list asserted | PASS: every rule fires on its positive, no negative fires | `test_security_engines.py::test_sap_pack_rules_fire_only_on_positive_examples`, `test_engines.py::test_pmd_apex_rules_on_the_salesforce_fixture`, `test_frameworks.py` |
| Mapping per relationship | Expected edge, classification and evidence line per relation kind | PASS | `test_frameworks.py::test_sap_mapping_is_evidence_backed`, `::test_salesforce_mapping_is_evidence_backed` |
| Malformed / missing metadata | Malformed `extensioninfo.xml`; custom object without metadata; missing version | PASS: partial capability + FAILED file coverage; `unresolved` link; `unknown_version` | `test_frameworks.py`, `test_p04_frameworks.py` |
| Unsupported versions | SAP `1905.30`; Salesforce `sourceApiVersion` 29.0 | PASS: `unsupported_version`, disclosed in UI notes | `test_frameworks.py` |
| Secure XML parsing | DOCTYPE, internal/external/parameter entities, entity expansion, XXE in object metadata | PASS: refused before use | `test_frameworks.py::test_xml_with_dtd_or_entities_is_refused`, Broken__c fixture |
| Generated code | Jalo class in `gensrc` | PASS: not reported | SAP Opengrep test |
| Dynamic configuration | Dynamic SOQL, beans/types defined by the platform, `${HYBRIS_BIN_DIR}` extension paths | PASS: counted or `declared`, never guessed | `test_frameworks.py` |
| Overlapping analyzers | Apex only through `pmd-apex` (Java PMD NOT_APPLICABLE on Apex); LWC through the existing ESLint (decorators parsed) | PASS | `test_p04_frameworks.py`, `test_engines.py::test_eslint_reads_lightning_web_component_decorators` |
| Evidence anchoring / no input modification | Evidence text equals the cited line; fixture digests before/after | PASS | `test_frameworks.py` |
| Real stack | Upload → scan → findings → graph summary with pack reports, both platforms | PASS | `services/worker/tests/test_p04_frameworks.py` |
| Version/capability coverage in the product | Platform support panel on reviews and Architecture; platform checks shown only when applicable | PASS | `apps/web/e2e/p04-frameworks.spec.ts` (light, dark, mobile screenshots `p04-*.png`) |
| Conditional platform validation | SAP build, Salesforce org deploy/tests | **Unavailable (conditional)**: no customer-authorized environment | FRAMEWORK_ADAPTERS.md "Validation profiles" |
| SME review | Domain expert sign-off | **Not done**: both packs `experimental` | — |

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint/format/types/contracts | `make check` | exit 0 |
| Tests | `make test` | exit 0 — 377 pytest (P04: framework packs 13, PMD Apex 1, LWC parsing 1, SAP Opengrep 1, real-stack 2) + 18 vitest; `mypy --platform linux` clean |
| Browser E2E | `make test-e2e` | exit 0 — 16/16 (foundation 7, P01 2, P02 2, UI tour 2, P03 1, P04 2) |

## Limitations

- Source-level only: no classpath, SAP type system, Spring context start-up, component scanning, bean overriding by load order, or Apex compilation. A mapped link is evidence in configuration, not proof at runtime.
- Not mapped in v1: SAP business processes, integration objects, CMS/SmartEdit, backoffice config, `*.properties` resolution; Salesforce Aura, Visualforce, profiles, sharing rules, Apex-to-Apex call graph, DML target types.
- Salesforce Code Analyzer 5.16.0 (`@salesforce/plugin-code-analyzer`, BSD-3-Clause) not integrated: its PMD engine is covered by the pinned PMD; Flow Scanner, Graph Engine, RetireJS and regex engines are deferred. `@lwc/eslint-plugin-lwc` 3.5.0 (MIT) not adopted yet.
- Candidates (`unbounded-result`, `missing-abort-check`) depend on workload; the guidance says so.

## Next tasks

1. SME review of both packs (then change status from `experimental`).
2. Evaluate Salesforce Code Analyzer's Flow Scanner and `@lwc/eslint-plugin-lwc` (pinned, isolated).
3. Conditional validation profiles in a customer-authorized environment (see FRAMEWORK_ADAPTERS.md).
