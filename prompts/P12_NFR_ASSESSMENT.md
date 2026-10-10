# Execute Phase 12 — NFR assessment

Read docs/NFR_ASSESSMENT.md, ARCHITECTURE_INTELLIGENCE.md, ADR 0018–0020 (architecture model,
rules, smells), ADR 0013 (framework packs), ADR 0012 (AI) and the P02/P10 reports. The goal:
answer a non-functional requirements questionnaire for an uploaded system with cited evidence,
tracked gaps, imported measurements and the team's attested answers, never presenting "nothing
found" as "requirement met".

## Deliver (slices, each shippable and tested)

1. **Questionnaire and readiness view.**
   - Built-in question bank: the owner's 9 aspects and 24 questions, mapped to ISO/IEC
     25010:2023; teams can add questions.
   - NFR profile per project: targets, regulations, platforms, attested answers; versioned and
     audited.
   - Existing findings tagged with aspects through the catalog; architecture smells and rule
     breaches included.
   - Readiness per aspect with the six answer states (docs/NFR_ASSESSMENT.md); questionnaire
     export (CSV) and a Markdown/HTML report.
2. **Configuration and infrastructure evidence.**
   - Kubernetes, Helm, Terraform and Dockerfile checks (Trivy misconfiguration checks offline,
     after verifying the exact version, licence and offline behaviour).
   - Spring Boot configuration (timeouts, pools, Actuator), resilience, observability and
     logging libraries from manifests.
   - Gaps become `nfr` engine findings with lifecycle; supporting evidence is cited.
3. **Code-pattern evidence** for performance, reliability and scalability:
   - calls without timeouts, unbounded queries, missing pagination, blocking calls, statelessness
     blockers;
   - SAP Commerce and Salesforce rows.
4. **Measured evidence import** (shared with P10 slice 5): k6/JMeter/Gatling summaries,
   OpenTelemetry traces, Lighthouse and axe reports, OWASP ZAP reports. Bounded and validated;
   compared with the profile's targets.
5. **AI NFR analyst** (P03 provider, project AI policy): cited draft answers, uncited claims
   rejected, labelled, team approval. Stakeholder report.
6. **Evaluation**: labelled fixtures per detector and labelled synthetic systems per question
   state; precision and recall recorded.

UI: plain language first (aspect cards, what is shown, what is missing, what we need from you),
evidence and technical details collapsed; light, dark and mobile.

## Mandatory tests

- **Honesty:** nothing found means "not determinable", never "met"; a team answer is labelled as
  attested, never as detected; compliance is never certified.
- **Detectors:** positive and negative fixtures per detector (Java, TypeScript, SAP Commerce,
  Salesforce, infrastructure files); generated and test code handled.
- **Lifecycle:** gaps are tracked issues; a profile or detector change re-evaluates honestly (per
  rule hashes, as in ADR 0019).
- **Imports:** malformed or oversized reports refused; targets met and missed computed correctly.
- **AI:** grounded (fake and fixture provider), injection stays data, uncited claims rejected,
  budgets hold.
- **Isolation:** profiles, answers and reports are project-scoped; viewers read, members answer.
- **Scale:** detector time and memory on medium and large synthetic repositories.

## Completion

Produce P12_REPORT.md with measured detector precision and recall, question-state accuracy and
limits, plus ADRs for the questionnaire model and the evidence detectors. AI quality needs the
live provider (BLOCKED for that part only); measured answers need customer files.
