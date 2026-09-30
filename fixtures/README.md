# Synthetic fixtures

Everything under `fixtures/` is **synthetic test data written for this project**. It is not customer
source, contains no real secrets (the `.env` value and crypto key are deliberately fake) and exists
to exercise intake, inventory, parsers and engines.

| Fixture | Purpose |
|---|---|
| `projects/seeded-mixed` | Java + JavaScript + TypeScript with seeded defects that the trusted PMD/ESLint rules detect, one Java and one TypeScript file with syntax errors (partial coverage), a fake `.env`, a `node_modules/` directory and a binary asset. Tests copy it to a temporary directory and add `AGENTS.md` from `untrusted-text/` |
| `projects/security-mixed` | Java/JS/TS security defects for the owned Opengrep rules, each next to a safe counterpart; `pom.xml` pins log4j-core 2.14.1 and `package-lock.json` pins lodash 4.17.15 (known-vulnerable on purpose). Fake credentials (a GitHub-token-shaped string and a private-key block) are **generated at test time** by `prepare_fixture()` and are never committed |
| `projects/graph-mixed` | Two Maven modules + an npm workspace (`web` → `shared`) with a JSONC `tsconfig.json` paths alias, re-exports, a dynamic non-literal import, an undeclared package, a missing type import and one file with a syntax error; expected graph classifications are asserted in `packages/analysis/tests/test_graph.py` |
| `projects/clean-mixed` | Java + TypeScript with no findings under the trusted rules (verified by tests) |
| `projects/sap-commerce-mixed` | Synthetic SAP Commerce CCv2 layout (`manifest.json` 2211.28): extensions `shopcore`/`shopfacades`/`shopocc`, a deliberate `shopimport`↔`shopexport` cycle and a malformed `shopbroken` descriptor; Spring XML, items.xml, ImpEx and Java with a positive and a negative example per SAP rule (incl. a generated `gensrc` Jalo class that must not be reported). No SAP code or SDK |
| `projects/salesforce-mixed` | Synthetic SFDX project (API 62.0): Apex classes/tests/trigger with a positive and a negative per Apex rule, one class at retired API 29.0, an LWC with Apex/schema imports and decorators, objects/fields, a record-triggered Flow, a permission set, custom metadata and an object file with an XXE attempt (must be refused) |

Expected findings are asserted in `packages/analysis/tests/test_engines.py` and the P01 E2E tests.

`untrusted-text/seeded-mixed-agents-instructions.txt` holds a synthetic prompt-injection text. It is stored as inert `.txt` data (not as `AGENTS.md`) so that coding agents working in this repository never load it as instructions. `crp_devtools.testing.fixture_projects.prepare_fixture()` writes it as `AGENTS.md` inside temporary copies only.
