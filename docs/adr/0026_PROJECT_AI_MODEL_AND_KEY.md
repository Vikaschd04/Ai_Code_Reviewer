# ADR 0026 — Each project chooses its AI model and uses its own API key

Status: accepted; implemented and verified (unit tests, API tests, a real worker run with the
project's key, browser journey). Date: 14 October 2026. Owner: repository owner. Request of
14 October 2026: "instead of putting in the environment variable we should give user option to
select which LLM they want to use for code fix … once user select any model, it should ask for a
api key … that api key should be only used for refactoring for that project and it would user own
api key. we should test the api key before saving if it works. it section should be display in
setting section when user turn on the ai review."

Context and constraints:

- ADR 0025 added two server connections, both set up in environment variables by the operator.
  Every project on a server therefore shares the operator's keys and spending. A user had to ask
  the operator to change a model.
- The owner wants the choice in the product. A project admin picks the provider (Anthropic or
  UnoRouter), then a model from that provider. They add their own key, and the key must be tested
  before it is saved.
- AGENTS.md rules that apply:
  - No source goes to an external model without an approved project policy and provider setup.
  - Never store secrets in memory or logs.
  - Never pass credentials to scanned code.
  - Keep provider integrations replaceable.
- Model facts were checked on 14 October 2026. Sources: Anthropic's models overview, pricing
  and migration notes (as bundled with the Claude API reference, cached 6 October 2026), and
  UnoRouter's public pricing list `https://api.unorouter.com/api/pricing`.
  - Current Claude IDs, from `claude-opus-5-5` back to `claude-haiku-4-5`; the full list is in
    `catalog.py`.
  - Opus 5.5, Fable 5.1, Mythos 5.1 and Sonnet 5.5 reject a forced `tool_choice` (any/tool) with
    a 400 error.
  - When thinking is on, the thinking blocks of a turn must be sent back unchanged.
  - The Anthropic API answers `GET /v1/models/{id}` without using any tokens.
  - UnoRouter's free models allow about one request per minute and 8K output tokens. The three
    named models (`glm-5.3:free`, `gemini-3.6-flash:free`, `deepseek-v4.1-flash:free`) are online
    and support tools.

Decision:

1. **Catalog** (`crp_analysis/ai/catalog.py`):
   - Anthropic offers the current Claude models with list prices, Opus 5.5 first ("Recommended").
     Mythos is left out because it is invitation only.
   - UnoRouter offers its free chat models that are online, use the OpenAI endpoint, support tools
     and output text. The list is read from the public pricing list and cached for an hour; a fixed
     list of the three named models is used when it cannot be read.
   - Base URLs are fixed per provider. Users never enter one; the operator can override them for
     tests only.
   - Endpoint: `GET /v1/ai/catalog`.
2. **Key test before saving** (`crp_analysis/ai/keytest.py`). No code is sent.
   - Anthropic: `GET /v1/models/{model}`, which uses no tokens.
   - UnoRouter: one chat completion with a toy `report_connection` tool. It must come back as a
     tool call, because refactorX's agents need tool calls.
   - Error messages are plain and never contain the key or the provider's text.
   - Each attempt is audited and counts towards a limit of 10 tests per project per hour
     (429 `key_test_limit`).
3. **Encrypted storage** (migration 0016, table `project_ai_models`, `crp_core/secret_box.py`).
   - Keys are sealed with AES-256-GCM. The key comes from HKDF-SHA256 over `CRP_SECRETS_KEY`
     (at least 32 characters, or a file through `CRP_SECRETS_KEY_FILE`).
   - The associated data is `project-ai-key:{project_id}:{provider}`, so a sealed key copied to
     another project or provider cannot be read.
   - Only the last four characters (`key_hint`) are stored in clear and returned. The key itself
     is never returned, logged or put in audit events.
   - Without `CRP_SECRETS_KEY`, saving is refused (409 `keys_unavailable`) and Settings says so.
     The Render Blueprint generates the secret automatically.
4. **Per-project use** (`project_ai_policies.connection = 'project'`):
   - `PUT /v1/projects/{id}/ai-model` needs a workspace admin and AI review switched on for the
     project. It tests the key first and saves only on success. It then makes the project use the
     model and records an audit event.
   - `DELETE` removes the model and key, and puts the project back on the server's default
     connection.
   - Runs record the `project` connection. The worker decrypts the key only when it runs. If the
     model was removed, or the key cannot be read, the run fails before anything is sent.
   - Server monthly budgets do not apply to the user's own key, but the per-run limits still do.
     Free models get spaced requests (61 s), longer runs and a 90 s cap on waiting out a
     `retry-after`.
5. **Settings UI** (`AiView` `ProjectModelCard`). It is shown once AI review is on and has:
   - a provider select and a model select (free models are marked);
   - the provider's data note, and a password field for the key with where to get one;
   - "Test and save".
   - Once saved, it shows "In use", the model, "Key ending …", when the key was tested, and the
     actions "Change model or key", "Remove key" and "Use this model" (shown when the project
     uses a server connection instead).
   - Server connections from ADR 0025 stay optional.
6. **Claude adapter compatibility** (`providers.py`):
   - For models that reject a forced tool choice, the adapter sends `tool_choice: auto` and adds
     an instruction to call the required tool. The orchestrator already treats a missing final
     tool call as a failure.
   - Content blocks the adapter does not model, such as `thinking`, are kept as `ProviderBlock`
     and sent back unchanged in later turns. They are never sent to other providers and never
     stored with their signature.

Alternatives considered:

- **Keys in the browser only, sent with each request.** Runs are queued and execute in the
  worker, so the key would have to travel with the job anyway. It would also be lost on reload.
  Rejected.
- **An external secret manager (Vault, a cloud KMS).** Better for multi-tenant production (P07).
  It is not available on the free hosting plan, so it is deferred. `SecretBox` is the replaceable
  seam.
- **Free-text model IDs and base URLs.** Rejected. Users would send code to arbitrary hosts, and
  models without tool support would fail later and less clearly.
- **A token-using test for Anthropic.** Not needed. The model lookup proves both the key and the
  model at no cost.

Consequences and migration/reversal approach:

- Migration 0016 adds the table and allows `project` in both connection CHECKs. A downgrade drops
  the table and moves `project` back to `primary`.
- Rotating `CRP_SECRETS_KEY` makes saved keys unreadable. Runs then fail with "add the key again",
  and nothing is sent. Re-entering the key fixes it.
- A free UnoRouter model forwards prompts to upstream providers that may keep them. The data note
  is shown before the key is added, and the consent to switch AI on still applies.
- Residual risk: anyone holding both the database and `CRP_SECRETS_KEY` can decrypt the keys.
  Keep the secret only in the host's secret settings.

Evidence: docs/validation/P03_REPORT.md (own model and key section). Tests:

- `packages/analysis/tests/test_ai_project_models.py`;
- `test_a_project_uses_its_own_tested_and_encrypted_key` (real stack: wrong key refused and
  nothing stored, ciphertext only, run with the user's key, removal, rate limit);
- `test_own_model_needs_admins_key_storage_and_stays_in_its_workspace`;
- `test_own_keys_cannot_be_saved_without_a_server_secret`;
- `e2e/ai-model.spec.ts`.

Affected contracts:

- `GET /v1/ai/catalog`;
- `GET|PUT|DELETE /v1/projects/{id}/ai-model`;
- project AI policy: `connection` may be `project`, and a new `ready` field;
- settings `CRP_SECRETS_KEY`, `CRP_SECRETS_KEY_FILE`, `CRP_AI_ANTHROPIC_BASE_URL`,
  `CRP_AI_UNOROUTER_BASE_URL` and `CRP_AI_CATALOG_LIVE`.
