# AI orchestration

Phase 3 adds AI to an already useful deterministic product. Provider/model/version are configurable and evaluated; no assumed paid account, fixed latest model or invented context size.

## Roles and contracts

Planner selects registered analysis capabilities and budgets. Retriever returns authorized exact-source spans, relevant graph neighborhoods, configuration, tests and version-matched standards. Investigator proposes structured findings. Verifier checks location, preconditions and alternative explanations using tools; a second model is only corroboration. Reporter summarizes verified facts and disclosed limitations. Patch author exists in Phase 5 with separate write boundaries.

Roles may be states in one typed orchestrator. Temporal owns durable lifecycle; avoid competing retry/checkpoint systems. Bound steps, tool calls, tokens/spend, time, retries and changed scope. On exhaustion preserve useful results and report unreviewed scope.

## Context packet

Include task/question, workspace/project/snapshot, applicable versions/rules, selected code spans with hashes, relevant graph edges/provenance, known limitations and tool permissions. Retrieve actual source before publishing a consequential claim. Do not store private chain-of-thought. Record concise rationale, source citations, decisions and tool evidence instead.

Output fields: finding_id candidate, category, source anchors, triggering conditions, impact, evidence class, severity rationale, recommendation/options, applicable source/version, validation needed, uncertainty. Reject nonexistent anchors and APIs; abstain when context is insufficient. A repository explanation must cite evidence and distinguish inferred business intent.

## Alibaba OCR

Evaluate a pinned CLI release/commit inside an isolated worker using full-file mode for non-Git snapshots. Verify non-Git behavior, config resolution, output schema, exclusion reporting, budget/cancellation, transcript handling and allowed endpoints/tools. Disable or isolate repository-controlled credential commands, plugins and MCP configuration. Do not assume its session viewer supplies shared product workflow.

Adopt if contract/evaluation/security gates pass; otherwise record the reason and implement a bounded direct-provider adapter. Both routes normalize into the same contracts. External provider calls only after project policy permits source disclosure.

## Cost and reliability

Count usage from provider/tool responses; distinguish unavailable counts and estimates. Separate input/output/cache/tool charges. Do not promise token savings without measurement. Cache using snapshot/context/rule/prompt/model identity. Retries obey provider guidance, jitter/backoff and idempotency; malformed structured output has a small bounded repair allowance.

Test no-key state, provider outage, budget exhaustion, prompt injection, malicious tool arguments, unauthorized graph retrieval, fake code references and irrelevant context. Fixtures are explicit test doubles; production scans never substitute their responses.

## Implementation status (P03)

Implemented as one typed orchestrator (ADR 0012): deterministic planner, bounded model investigator with five read-only snapshot tools, deterministic anchor verifier, and stored runs/findings; Temporal (standard) or the inline runner (lite) owns the lifecycle and never retries paid calls. Alibaba OCR was evaluated and not adopted (docs/validation/P03_OCR_EVALUATION.md). Labelled evaluation: `fixtures/ai-eval/`, `crp-dev ai-eval`. Live quality measurement awaits a configured provider (docs/validation/P03_REPORT.md).

## Fix suggestions (P08, ADR 0016)

A fourth run kind, `fix`, asks for up to `CRP_AI_FIX_MAX_CANDIDATES` (default 3) candidate fixes of one finding in a fix workspace. It runs through the same gate, budgets, accounting, Temporal or inline lifecycle and fenced untrusted context as the other runs. The differences:
- The prompt (`rx-ai-fix-v1`) shows the file as it is in the workspace. The read tools see that text through an overlay; every other file reads as uploaded.
- The model submits whole-line edits that quote the original lines (`submit_fixes`); it cannot name another file.
- Each candidate is then checked deterministically (`crp_analysis/fixes/ai_candidates.py`):
  - the exact lines must match;
  - the strict change policy applies (no suppressions, no weakened, skipped or focused tests, no configuration changes, size limit);
  - the P05 ladder runs on copies with the trusted engines: parse, original check gone, nothing new.
- Only candidates that pass can be applied, by a person, as an `ai` change with the finding recorded.
- Model agreement never counts as verification, and live quality is not measured until the owner's key is configured.

