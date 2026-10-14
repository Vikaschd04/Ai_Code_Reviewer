# ADR 0025 — Two AI connections: a primary model and a cheaper or free one

Status: accepted; implemented and verified (unit tests, API test, a real worker run on the second
connection, browser journey). Date: 14 October 2026. Owner: repository owner (request of
14 October 2026: "integrate another AI model for fixing and other AI related stuff along with
Anthropic Claude as it is expensive; we keep another free model for testing").

Context and constraints:

- One server-wide provider (ADR 0012) meant every AI feature used the paid model, including
  tests and experiments.
- The owner proposed UnoRouter (`https://api.unorouter.com/v1`), an OpenAI-compatible gateway
  with free model variants (`:free`). Checked on 14 October 2026:
  - `glm-5.3:free` answered a chat completion with a standard OpenAI tool call
    (`finish_reason: tool_calls`) and accepted `max_completion_tokens`, so the existing
    OpenAI-compatible adapter and tool-using agents work unchanged (test call with a toy tool; no
    source code sent).
  - Free models allow about one request per minute per model and cap output at 8K tokens
    (UnoRouter documentation and listings).
  - Privacy policy: request content "is transmitted to the upstream provider that serves the
    model", and "each provider handles data under its own terms, and some may retain or train on
    what they receive"; UnoRouter itself says it does not train on content. No legal entity or
    jurisdiction is named; services are provided without warranties.
- AGENTS.md: no source to an external model without an approved project policy and provider
  setup; keys only in the server environment.

Decision:

1. **Two connections** (`crp_analysis/ai/config.py`): `primary` (`CRP_AI_*`, unchanged) and an
   optional `alt` (`CRP_AI_ALT_*`: provider, model, base URL, key or key file, label, data note,
   request pacing, retry-after cap, retries, run timeout, output tokens, monthly token limit,
   prices). Unset alternate limits fall back to the primary's. `CRP_AI_DEFAULT_CONNECTION` picks
   what new project policies start with (falling back to whichever is usable).
2. **Per-project choice** (migration 0015): `project_ai_policies.connection`. A workspace admin
   chooses the connection when switching AI on or later ("Change model"); the consent names the
   chosen connection and shows its data note. Choosing a connection that is not set up is refused
   (422 `connection_unavailable`); changes are audited in `ai_policy_events`.
3. **Runs use the project's connection**: question, file and finding reviews, the advisor plan and
   AI fix suggestions record `ai_runs.connection`; the worker resolves that connection and refuses
   to send anything if the project's connection changed after the request
   (`ai_connection_changed`).
4. **Budgets and usage per connection**: monthly token and cost checks count only that
   connection's calls; `GET /v1/ai/status` lists the connections with label, model, availability,
   data note, `free` (both prices zero), `default` and this month's usage.
5. **Free-tier pacing** (`ClientOptions`): `min_interval_seconds` spaces requests per endpoint and
   model in a process; `max_retry_after_seconds` lets a connection wait out a provider's
   `retry-after` (primary keeps 30 s; alternate defaults to 90 s).

Alternatives considered:

- Routing by task (cheap model for questions, paid for fixes): not now; per-project choice is
  simpler to understand, keeps one consent per project, and covers "free for testing".
- Any number of connections: not needed for the request; the code keeps connections behind
  `CONNECTIONS`, so more can be added later.
- Using UnoRouter for customer code by default: rejected. Its free tier forwards prompts to
  upstream providers that may retain or train on them; the data note and consent make that choice
  explicit per project.

Consequences and migration/reversal approach:

- Migration 0015 adds two columns with default `primary`; existing projects and runs keep using
  the primary connection. Reversal: downgrade the migration and unset `CRP_AI_ALT_*`.
- Free models are weaker: answers and fix candidates still go through the same citation checks
  and fix validation ladder, so they fail closed (fewer kept steps or passing fixes), never open.
- Pacing is per process: two processes calling the same free model can still hit 429s, which are
  retried after the provider's advised delay.

Evidence: docs/validation/P03_REPORT.md (AI connections section); tests
`test_two_connections_resolve_with_their_own_label_note_budget_and_pacing`,
`test_free_tier_pacing_spaces_requests_and_waits_out_rate_limits`,
`test_projects_choose_an_ai_connection_and_runs_use_it`, `test_ai_run_on_the_alternate_connection`,
`e2e/insights.spec.ts` (free test model chosen in Settings; the plan names its model).
Sources: UnoRouter home page, privacy policy (unorouter.com/en/privacy) and terms
(unorouter.com/en/terms), read 14 October 2026; UnoRouter's blog on aggregated free models.

Affected contracts: `GET /v1/ai/status` (`connections`), project AI policy (`connection`,
`connection_label`), AI runs (provider and model of the chosen connection); settings
`CRP_AI_ALT_*`, `CRP_AI_LABEL`, `CRP_AI_DATA_NOTE`, `CRP_AI_DEFAULT_CONNECTION`.
