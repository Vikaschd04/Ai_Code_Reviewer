# Standards and guidance registry

Store reference ID, official URL, publication/retrieval dates, content hash, applicable language/framework/API/runtime ranges, license/use constraints, status (draft/final/deprecated), rule links, reviewer and next review date. Preserve historical versions used by scans.

Official sources guide security and compatibility; team conventions define local style. Do not let model training knowledge decide what is latest. Check current documentation during implementation. Draft guidance is not silently treated as adopted final policy.

Update flow: discover change → retrieve approved source → assess applicability → author/propose rule → positive/negative fixtures → evaluation → owner approval → staged rollout. AI can help draft/explain; it cannot promote arbitrary web content to executable policy.

Precedence: approved mandatory security policy, supported platform compatibility, agreed team conventions, optional modernization. Conflicts become explicit decisions. Recommending newer syntax to an incompatible runtime is a defect in this product.

Reference adapters must not execute instructions embedded in documents, follow arbitrary links from customer comments or send source to unapproved websites. Store concise attributed reference metadata/excerpts according to usage rights; avoid copying entire proprietary manuals.

## Registered references (retrieved 30 September 2026)

Content hashes are not recorded for these pages: they are rendered dynamically and the rules cite
them for guidance, not as executable policy. Reviewer: development agent (pending SME review).
Next review: with the next pack version or 31 March 2027, whichever is earlier.

| ID | Source | Applies to | Used by | Status |
|---|---|---|---|---|
| SAP-INTERCEPTORS | [SAP Help: Interceptors](https://help.sap.com/docs/SAP_COMMERCE/d0224eca81e249cb821f2cdf45a82ace/8bfbf43e8669101480d0f060d79b1baa.html) ("use registerElementFor() instead of modelService.save()/remove()") | SAP Commerce 2105–2211 | `crp.sap.interceptor.persisting-side-effect` | final |
| SAP-JALO | [SAP Help: Jalo Layer](https://help.sap.com/docs/SAP_COMMERCE/d0224eca81e249cb821f2cdf45a82ace/8c00066686691014a5a5d19875a1525b.html) | SAP Commerce 2105–2211 | `crp.sap.jalo.deprecated-api` | final |
| SAP-JOBPERFORMABLE | [AbstractJobPerformable API (2105)](https://help.sap.com/doc/9fef7037b3304324b8891e84f19f2bf3/2105/en-us/de/hybris/platform/servicelayer/cronjob/AbstractJobPerformable.html) (`clearAbortRequestedIfNeeded`) | SAP Commerce 2105–2211 | `crp.sap.cronjob.missing-abort-check` | final |
| SF-API-RETIREMENT | [Salesforce Help 000389618: Platform API Versions 21.0 through 30.0 Retirement](https://help.salesforce.com/s/articleView?id=000389618&language=en_US&type=1) (Summer '25) | Salesforce API 21.0–30.0 | `crp.sf.metadata.retired-api-version`, Salesforce version status | final |
| PMD-APEX-7.27.0 | [PMD 7.27.0 Apex rule documentation](https://docs.pmd-code.org/pmd-doc-7.27.0/pmd_rules_apex_security.html) | PMD 7.27.0 | `crp-pmd-apex-v1` (11 rules) | final |
| CWE | CWE-89, 532, 770, 1047, 1050 (cwe.mitre.org) | language-neutral | SAP and Java hygiene rules | final |
| 12FACTOR-CONFIG | [The Twelve-Factor App: Config](https://12factor.net/config) | language-neutral | `crp.java.config.hardcoded-environment-url` | final |

