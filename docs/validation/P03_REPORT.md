# Phase P03 validation report — Bounded AI review and repository Q&A

- Date: 30 September 2026. Actor: Claude Code development agent (Opus 5.5), single agent.
- Status: **BLOCKED on the live-provider gate**; every offline component is implemented and
  tested. The owner chose the providers and the disclosure policy (ADR 0012) but has not yet
  configured an API key, so no real model has been called and no model quality is claimed.
- Code: branch `main`, commits `92dd833` (foundations), `21ca0a1` (runs), `6165692` (UI), plus
  the evaluation/report commit that adds this file.
- Environment: macOS 26.5.2 arm64; Python 3.14.3; Node 22.22.0; pnpm 11.20.0; PostgreSQL 18.6;
  Temporal CLI 1.9.1 (Server 1.32.0); temporalio 1.33.0; FastAPI 0.141.1; httpx 0.28.1; Google
  Chrome (Playwright). Engines as in P02.
- Model/prompt versions: prompt `rx-ai-v1`; default Anthropic model `claude-opus-5-5`
  (configurable); OpenAI-compatible model must be named by the operator. Test provider in E2E and
  offline evaluation: `refactorx-e2e-fake` (labelled; `crp_devtools/testing/fake_ai.py`).
- Provider/network policy: calls only from the worker/inline runner to `CRP_AI_BASE_URL` (https,
  or http on localhost only), only for projects whose AI policy an admin switched on, only masked
  excerpts of the run's frozen upload. No MCP, shell or model-chosen endpoints.

## Implemented behavior

- **Adapter and settings** (`crp_analysis/ai/{models,providers,config}.py`): Anthropic and
  OpenAI-compatible clients behind one contract; typed errors; bounded retries honouring
  `retry-after`; usage per call (provider-reported or flagged estimates); `CRP_AI_*` settings with
  run limits, monthly token/cost caps and owner-set prices (no assumed prices). No key → a
  plain-language reason for users and a setup hint for operators; automatic reviews unaffected.
- **Policy** (migration `0005_ai_review`): per-project switch, off by default, admin-only,
  version-checked, audited (`ai_policy_events`); the UI requires an explicit confirmation.
- **Retrieval and tools** (`snapshot.py`, `tools.py`): read-only, snapshot-scoped `read_file`,
  `search_code`, `list_symbols`, `graph_neighbors`, `list_findings` with strict argument schemas;
  every excerpt masked, fenced with path, line range and content hash, and instruction files
  (AGENTS.md, CLAUDE.md, MCP configs, …) flagged as untrusted data. Finding reviews include the
  version-matched rule guidance from the catalog.
- **Planner → investigator → verifier** (`prompts.py`, `orchestrator.py`, `verify.py`):
  deterministic first message; bounded loop (model calls, tool calls, tokens, time, optional cost;
  last call reserved for submitting; two repair attempts); structured `submit_answer` /
  `submit_review`; deterministic anchor check → `verified_anchor` / `hypothesis` / `rejected`.
  Abstention and uncertainty are first-class; severity, evidence and confidence are separate;
  deterministic findings are never altered.
- **Runs** (`crp_worker/ai_run.py`, API `routes/ai.py`): create/list/get/cancel; policy, provider,
  monthly cap and target re-checked at creation and before the first call; usage recorded per call;
  never retried (interrupted → FAILED); Temporal workflow and lite inline runner; transcripts stored
  masked and deleted with the project.
- **UI** (`AiView`, `AiRunPage`, `Ai.tsx`): project "AI review" tab (not-set-up state, opt-in,
  ask, review up to five files, earlier runs, monthly allowance), run page (answer or review,
  "Checked against your code" / "Not verified" badges, discarded suggestions collapsed, verdict,
  limits, technical details with provider, model, prompt version, tokens, cost), "Ask AI to check
  this" on findings. The sidebar's data note changes when AI is set up.
- **Evaluation**: labelled set `fixtures/ai-eval/` (8 dev + 4 held-out, 9 defective + 3 clean,
  Java/Python/JS/TS) with criteria written before any run; harness `crp-dev ai-eval [--live]`.
- **Alibaba OCR**: evaluated in isolation (`crp-dev ocr-eval`); not adopted — see
  [P03_OCR_EVALUATION](P03_OCR_EVALUATION.md).

## Mandatory checks (P03 prompt)

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Provider success / timeout / rate limit / malformed | Scripted HTTP transports per provider | PASS (offline doubles) | `test_ai_providers.py` (9) |
| No-key state | Status, capability, run creation, UI, `crp-dev ai-eval --live` without configuration | PASS: actionable reason, `not_configured`, 409 `ai_unavailable`, live eval exits 2 | `test_ai_api.py`, `test_health_and_auth.py`, CLI run |
| Source transmission policy | Off by default; member cannot enable; runs refused while off; re-check before first call; https-only endpoints | PASS | `test_ai_api.py`, `test_ai_providers.py::test_setup_explains_missing_configuration` |
| Fake / mismatched anchors | Unknown path, bad range, wrong quote, unquoted; invented finding in an end-to-end review | PASS: `rejected` / `hypothesis`; invented finding hidden as discarded | `test_ai_orchestrator.py`, `test_ai_run.py` |
| Unauthorized context | Other workspace's runs/projects; tool paths outside the upload | PASS: 404 without revealing existence; traversal refused | `test_ai_api.py::test_runs_of_other_workspaces_are_invisible`, `test_malicious_tool_arguments_are_refused` |
| Secret redaction | Planted key in source; provider requests and stored transcript inspected | PASS: never sent, never stored | `test_secret_values_never_reach_the_provider`, `test_ai_run.py` |
| Prompt injection (comments/docs/AGENTS/MCP) | Injected instructions and fence-closing text in source; model asks for unknown tools | PASS: stays fenced data, instruction files flagged, no extra tools | `test_prompt_injection_stays_data_and_cannot_add_tools`, `test_fences_cannot_be_closed_from_source`, `test_ai_run.py` (AGENTS.md flagged in the real request) |
| Malicious tool arguments | Traversal, absolute path, extra field, wrong type, unknown tool, oversized query | PASS: refused with a tool error, nothing read | `test_malicious_tool_arguments_are_refused` |
| Stale context | Quote from a newer version checked against the run's upload | PASS: quote mismatch; stored hash names the version checked | `test_stale_context_does_not_verify_against_another_version` |
| Cancellation | Cancel during a model call on the real stack (Temporal and lite runner); cooperative cancel in the orchestrator; OCR SIGTERM | PASS: CANCELED, no call after the request | `test_ai_run.py::test_ai_run_cancellation_and_budget_stop`, `::test_ai_run_cancellation_on_the_lite_profile`, `test_cancellation_timeout_and_provider_errors`, OCR evaluation |
| Spend cap | Per-run call/token/cost limits; monthly token cap at creation and before the first call | PASS: BUDGET_EXHAUSTED / 429 `ai_monthly_limit` | `test_token_and_cost_limits_stop_the_run`, `test_ai_run_cancellation_and_budget_stop`, `test_monthly_token_limit_blocks_new_runs` |
| Model agreement ≠ verification | Only deterministic anchor checks set evidence; UI explains that "Checked against your code" means the cited lines exist, not that the conclusion is right | PASS (design + UI) | ADR 0012, `AiRunPage` |
| Labelled evaluation set | 12 cases, dev/held-out, clean twins, criteria in `fixtures/ai-eval/README.md` | PASS (set and harness) | `test_ai_eval.py` |
| **Live provider: precision, labelled recall, anchor validity, tokens, cost** | `crp-dev ai-eval --split all --live` | **BLOCKED**: no approved provider key configured | — |

Offline harness run (`.local/ai-eval/ai-eval-offline-20260930T121954Z.json`, fake provider):
12/12 cases completed, anchor validity 1.0, precision 0.25, labelled recall 0.333, 3 false
alarms on clean cases. These numbers only show the pipeline and scoring work: the fake always
comments on a file's first statement. **They are not model quality.**

## Checks

| Check | Command | Outcome |
|---|---|---|
| Lint/format/types/contracts | `make check` | exit 0 |
| Tests | `make test` | exit 0 — 357 pytest (incl. AI API 9, AI worker end-to-end 4 on Temporal and the lite runner, orchestrator 13, providers 9, evaluation 5) + 18 vitest |
| Browser E2E | `make test-e2e` | exit 0 — 14/14 (foundation 7, P01 2, P02 2, UI tour 2, P03 1) with the labelled fake provider; screenshots `apps/web/test-results/screens/p03-*.png` (light, dark, mobile) |
| OCR evaluation | `crp-dev ocr-eval` | recorded `report-20260930T121719Z.json` |

## Limitations

- No live model call has been made; quality, latency and real token/cost figures are unknown.
- Retrieval is lexical plus the P02 graph; no embeddings. Only the selected question, finding or up
  to five files are reviewed per run — never a claim of whole-repository AI review.
- Secret masking is heuristic (K-P01-02); a secret the redactor misses would be sent for projects
  with AI switched on.
- A model call in flight when a run is cancelled may still be billed by the provider.
- AI findings are exported per run and shown on the finding they assess, but never become tracked issues; no fixes (P05).

## Next tasks

1. Owner: set `CRP_AI_PROVIDER`, `CRP_AI_API_KEY` (and `CRP_AI_MODEL` for OpenAI-compatible) on
   the server (docs/DEPLOYMENT.md "AI review"), then run `crp-dev ai-eval --split all --live`
   locally with the same settings and record the numbers here; that closes the P03 gate.
2. Done (30 September 2026, later): per-run JSON/SARIF export (`GET /v1/ai-runs/{id}/export`, schema-validated in `test_ai_export.py` and on real runs in `test_ai_run.py`) and earlier second opinions on the finding page. AI findings intentionally do not become tracked issues (no deterministic re-check).
3. Re-evaluate OCR only when the conditions in P03_OCR_EVALUATION.md are met.

## AI connections (14 October 2026; ADR 0025)

Owner request: add a cheaper or free model next to Claude, for testing. Implemented: a second
connection (`CRP_AI_ALT_*`), per-project choice with consent that names the connection, runs and
budgets per connection, pacing for free tiers (Settings, `GET /v1/ai/status`, migration 0015).

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| UnoRouter compatibility | One chat completion with a toy tool to `glm-5.3:free` (no source code): HTTP 200 in 2 s, `finish_reason: tool_calls`, valid arguments, `max_completion_tokens` accepted | PASS | Manual check, 14 October 2026 |
| Configuration | Both connections resolve with their own label, note, limits, prices and hints; plain http refused; only-alternate servers default to it; keys never printed | PASS | `test_two_connections_resolve_with_their_own_label_note_budget_and_pacing` |
| Free-tier pacing | Second request waits for the interval; a 429 with retry-after 70 s is waited out under a 90 s cap | PASS | `test_free_tier_pacing_spaces_requests_and_waits_out_rate_limits` |
| API | Status lists both connections (no key in the response); a project switches to the free model; its run uses that model; an unavailable connection is refused and a project left on it gets `ai_unavailable` | PASS | `test_projects_choose_an_ai_connection_and_runs_use_it` |
| Worker | A real run on the alternate connection calls its host with its key and model; usage counts only against it | PASS | `test_ai_run_on_the_alternate_connection` |
| Browser | Settings shows both models with the data note; the free model is chosen; the advisor plan names its model | PASS | `e2e/insights.spec.ts` (`ai-connection.png`) |

Not measured: the free model's answer and fix quality on the labelled AI evaluation set
(`crp-dev ai-eval --live` needs the owner to run it with the key; the free tier's one request per
minute makes a full run take hours).

## A project's own model and key (14 October 2026; ADR 0026)

Owner request: choose the provider and model per project in Settings, add your own API key,
test it before saving, use it only for that project. Implemented: catalog, key test, encrypted
storage (migration 0016), per-project connection `project`, worker decryption at run time,
Settings card; Claude adapter made compatible with current models.

| Check | Procedure | Outcome | Evidence |
|---|---|---|---|
| Encryption | Round trip; fresh nonce; another project, provider, server secret or a tampered token cannot decrypt; no or short server secret means no storage | PASS | `test_keys_are_encrypted_and_bound_to_their_project_and_provider` |
| Catalog | Claude list (Opus 5.5 first, no Mythos, prices); UnoRouter filter keeps only free, online, OpenAI-endpoint chat models with tools and text output, newest first, output capped at 8K; fallback when the list cannot be read | PASS | `test_catalog_offers_current_claude_models_and_free_tool_capable_unorouter_models`, `test_live_unorouter_list_falls_back_when_unreadable` |
| Key tests | Anthropic: GET model, no body, 401/404/429 mapped to plain messages without the key; UnoRouter: a reply without the tool call is refused | PASS | `test_anthropic_key_check_reads_the_model_and_maps_errors`, `test_unorouter_key_check_needs_a_tool_call` |
| Claude compatibility | Opus 5.5 gets `tool_choice: auto` plus the instruction; Opus 4.8 keeps the forced choice; thinking blocks echoed unchanged and kept in order; OpenAI-compatible requests never get them | PASS | `test_claude_requests_keep_thinking_blocks_and_avoid_forced_tool_choice_where_rejected`, `test_claude_responses_keep_thinking_blocks_in_order` |
| Real stack | Server without AI: saving before AI is on refused; wrong key refused, nothing stored; unknown model refused; working key saved as `v1.` ciphertext only; policy ready on `project`; a question run calls Anthropic with the user's key and model, with a thinking block in the reply, and succeeds; removing the key stops AI (409); the 11th key test in an hour gets 429 | PASS | `test_a_project_uses_its_own_tested_and_encrypted_key` |
| Authorization | Choosing `project` without a model 422; bad keys 422; unknown provider 400; another workspace 404 on GET/PUT/DELETE; members see but cannot change (403); no server secret 409 | PASS | `test_own_model_needs_admins_key_storage_and_stays_in_its_workspace`, `test_own_keys_cannot_be_saved_without_a_server_secret` |
| Browser | Settings after switching AI on: Claude list, UnoRouter free list, data note; wrong key shows the reason; working key "In use" with "Key ending …"; the page never contains the key; a question runs on `glm-5.3:free`; Remove key; light, dark, 390 px without overflow | PASS | `e2e/ai-model.spec.ts` (`ai-model-form.png`, `ai-model-saved.png`, `ai-model-saved-dark.png`, `ai-model-mobile.png`) |

Gates (14 October 2026, macOS arm64): `make check` exit 0; `make test` 616 pytest + 30 vitest passed; `make test-e2e` 22/22 passed.

Not measured: live calls with a real user key (the tests use labelled fakes for Anthropic and
UnoRouter). The owner can check by adding their key in Settings; the test call uses no tokens on
Anthropic and one small request on UnoRouter.
