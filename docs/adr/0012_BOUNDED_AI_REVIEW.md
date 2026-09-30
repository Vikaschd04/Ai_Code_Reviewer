# ADR 0012 — Bounded, opt-in AI review with deterministic citation checks

Status: accepted. Date: 30 September 2026. Owner: repository owner (provider choice and the
per-project opt-in were decided by the owner on 30 September 2026); implemented by the development
agent.

Context and constraints:

- P03 adds AI questions and reviews on top of the deterministic product. Source may leave the
  server only under an approved project policy and provider setup (AGENTS.md).
- The owner chose both Anthropic and OpenAI-compatible providers, switchable by configuration,
  with the key supplied by the operator (Render environment variable or an owner-only file), and
  source sharing **per project, off by default**.
- The free hosting profile has 512 MB and 0.1 CPU; runs must be cheap to host and never repeat
  paid calls by accident.
- Customer code is untrusted: comments, READMEs and AGENTS.md/CLAUDE.md/MCP files may contain
  instructions aimed at the model.

Decision:

1. **Provider-neutral adapter** (`crp_analysis/ai/providers.py`): one request/response contract,
   `AnthropicClient` (Messages API) and `OpenAICompatibleClient` (chat completions; bearer or
   `api-key` header, `max_completion_tokens` or `max_tokens`). Retries only on 408/409/429/5xx/529,
   timeouts and network errors, honouring `retry-after` (capped at 30 s) with jittered backoff;
   typed error codes; keys never logged or returned. Base URLs must use https (plain http only for
   localhost). Missing configuration yields a plain-language reason plus an admin hint; automatic
   reviews keep working.
2. **Per-project disclosure policy** (`project_ai_policies`, audited in `ai_policy_events`): off by
   default; only workspace admins/owners can switch it on, with an explicit confirmation in the
   UI. Re-checked when a run is created and again right before the first call.
3. **Deterministic planner → bounded investigator → deterministic verifier**
   (`prompts.py`, `orchestrator.py`, `verify.py`). The planner builds the first message from the
   question, a file overview and keyword matches, the finding with its rule guidance and
   surrounding code, or the chosen files. The model may use only five read-only tools over the
   run's frozen snapshot (`read_file`, `search_code`, `list_symbols`, `graph_neighbors`,
   `list_findings`) with strict argument schemas, then must call `submit_answer` or
   `submit_review` (structured output; two repair attempts). Hard limits: model calls, tool calls,
   tokens, wall time, optional cost per run, and a monthly token/cost cap; the last call is
   reserved for submitting. Every excerpt is masked with the secret redactor, fenced as untrusted
   `<source path lines sha256>` data, and instruction files are flagged.
4. **Evidence, not agreement**: each citation is checked against the snapshot (file exists, lines
   exist, quoted code matches after the same masking, ±2 lines drift). Results are
   `verified_anchor`, `hypothesis` or `rejected`; severity, confidence and evidence stay separate.
   Rejected suggestions are hidden by default; deterministic findings are never changed by a model.
5. **Durable, never-retried runs**: `ai_runs`, `ai_calls` (usage recorded per call as it happens,
   so caps hold across crashes) and `ai_findings`. Temporal workflow (standard) and the inline
   runner (lite) share activities; the execute activity has `maximum_attempts=1`, and a run found
   RUNNING after a restart ends FAILED "interrupted" instead of being repeated. Cancellation is
   cooperative (checked between calls) and via workflow cancellation.
6. **Transcripts** (`ai-runs/{run}/transcript.json`, masked content only) are kept for audit when
   `CRP_AI_KEEP_TRANSCRIPTS` is true and deleted with the project.
7. **Alibaba open-code-review is not adopted in P03** (measured in P03_OCR_EVALUATION.md); the
   direct-provider path above is the delivered review path. Both would normalise into the same
   run/finding contracts if OCR is adopted later.

Alternatives considered:

- *A single provider*: rejected by the owner (switchable providers wanted).
- *Workspace-wide or default-on sharing*: rejected; disclosure is a per-project admin decision.
- *Agent frameworks with shell/MCP access*: rejected; tools are fixed, read-only and snapshot-scoped.
- *Retrying failed runs automatically*: rejected; it would repeat paid calls.
- *Adopting Alibaba OCR as the engine now*: deferred; on a raw upload it sent an excluded `.env`
  secret and unmasked keys, obeyed a repository-planted rule file and stored full transcripts.

Consequences and migration/reversal approach:

- Migration `0005_ai_review` adds five tables; downgrade drops them. Removing the provider
  configuration disables AI without touching stored runs.
- Answers are only as good as the configured model; quality is unmeasured until a live provider is
  configured (`crp-dev ai-eval --live`). The UI says what "checked against your code" means.
- Prices are never assumed: cost is shown only when the operator configures per-token prices.

Evidence and source/version references:

- Anthropic Messages API (`anthropic-version: 2023-06-01`), OpenAI-compatible chat completions;
  default Anthropic model `claude-opus-5-5`, no default OpenAI-compatible model.
- Prompt version `rx-ai-v1`. Tests: `packages/analysis/tests/test_ai_providers.py`,
  `test_ai_orchestrator.py`, `services/api/tests/test_ai_api.py`,
  `services/worker/tests/test_ai_run.py`, `tools/devtools/tests/test_ai_eval.py`,
  `apps/web/e2e/p03-ai.spec.ts`. Report: docs/validation/P03_REPORT.md.

Affected contracts, phases and tests:

- API: `GET /v1/ai/status`, `GET/PUT /v1/projects/{id}/ai-policy`,
  `POST/GET /v1/projects/{id}/ai-runs`, `GET /v1/ai-runs/{id}`, `POST /v1/ai-runs/{id}/cancel`;
  `project_id` on finding detail; capability `ai_investigation` is `available` or
  `not_configured`.
- P05 (validated fixes) builds on runs and verified anchors; P07 must add per-tenant keys and
  provider allow-lists.
