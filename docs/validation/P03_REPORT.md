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
- AI findings are not yet linked to issues or included in exports; no fixes (P05).

## Next tasks

1. Owner: set `CRP_AI_PROVIDER`, `CRP_AI_API_KEY` (and `CRP_AI_MODEL` for OpenAI-compatible) on
   the server (docs/DEPLOYMENT.md "AI review"), then run `crp-dev ai-eval --split all --live`
   locally with the same settings and record the numbers here; that closes the P03 gate.
2. Link verified AI findings to issues and exports; add AI runs to the audit view.
3. Re-evaluate OCR only when the conditions in P03_OCR_EVALUATION.md are met.
