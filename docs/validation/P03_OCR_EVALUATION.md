# P03 evaluation — Alibaba open-code-review (OCR) as the AI review engine

- Date: 30 September 2026. Actor: Claude Code development agent (Opus 5.5), single agent.
- Reproduce: `uv run crp-dev ocr-eval` (macOS only; writes `.local/eval/alibaba-ocr/report-*.json`).
  Recorded run: `report-20260930T121719Z.json`.
- Decision: **not adopted in P03** (deferred). refactorX's own bounded direct-provider path
  (ADR 0012) is the delivered review path.

## What was evaluated

| Item | Value |
|---|---|
| Package | npm `@alibaba-group/open-code-review@1.12.11` + platform package `@alibaba-group/ocr-darwin-arm64@1.12.11` (Go binary, 55 MB) |
| Licence | Apache-2.0 (both package manifests) |
| Integrity | Binary SHA-256 `5580b7b2…cd204` equals the value in the release's `sha256sum.txt` (github.com/alibaba/open-code-review, tag v1.12.11); `version`: `v1.12.11 (a758d9cb) built 2026-09-29T14:33:46Z` |
| Install | `npm install --ignore-scripts` (its `postinstall` downloader not run); the binary is called directly, bypassing the Node launcher, which otherwise starts a background self-update check (`OCR_NO_UPDATE` unset) and keeps state in `~/.opencodereview` |
| Isolation | `sandbox-exec`: outbound network denied except loopback, writes only to a throwaway `HOME`, empty environment (no credentials); OCR configured only through `OCR_LLM_URL/TOKEN/MODEL` |
| Model | A loopback probe speaking the Anthropic Messages protocol (OCR's default for `OCR_LLM_*`). It issues fixed tool calls per file: `file_read ../../…/etc/hosts`, `file_read /etc/hosts`, `file_read .env`, `code_search "sk-live-canary"`, one `code_comment`, `task_done`. It measures handling of untrusted input, not review quality |
| Input | Non-Git copy of the `seeded-mixed` fixture plus two canaries: a key `sk-live-canary-…` in `web/src/config.js` and a repository rule file `.opencodereview/rule.json` containing `CANARY-RULE-7f3a` |

Two runs: OCR on the **raw upload folder**, and on a **prepared copy** (only files refactorX
reviews, repository OCR config and AI instruction files removed, every line masked with
refactorX's secret redactor, line numbers preserved).

## Measured results

| Gate | Raw upload | Prepared copy | Notes |
|---|---|---|---|
| Non-Git full-file mode (`ocr scan`) | PASS: status `success`, 12 files | PASS: 11 files | No Git needed; `.env`, binaries and docs excluded by OCR itself |
| Path traversal / absolute paths in `file_read` | PASS: `../` refused as outside the repository; `/etc/hosts` resolved inside the repository | PASS | |
| File excluded as a secret still readable by the agent | **FAIL**: `.env` value sent to the model | PASS (file not present) | OCR excludes `.env` from review but its `file_read` tool still returns it |
| Secrets masked before sending | **FAIL**: planted key sent verbatim (file content and `code_search` results) | PASS | OCR has no masking; the wrapper must mask |
| Repository config isolation | **FAIL**: `.opencodereview/rule.json` from the upload injected into prompts | PASS (removed) | Untrusted uploads can steer the reviewer through OCR's project rules |
| Transcript retention | Full transcripts with secrets in `~/.opencodereview/sessions` (623 KB) | Masked transcripts (588 KB) | Controllable only with a per-run `HOME` deleted afterwards |
| Output schema | JSON: `status, llm, summary, tool_calls, comments, project_summary, session_id`; comment: `path, content, existing_code, start_line, end_line, category` | same | No severity, confidence or evidence class; in a manual run, comments whose `existing_code` did not match got `start_line` 0 |
| Budget | `--max-tokens-budget`, `--max-tools`, `--timeout` available | | Per file: one planning request plus an agent loop (and optional dedup/summary passes). With the probe's fixed six-step script: 94 requests for 12 small files (not a natural-usage measurement) |
| Cancellation (SIGTERM mid-run) | PASS: stopped in 0.01 s, no requests after the signal, no partial output | | |

## Why it is not adopted now

1. On raw input it breaks three P03 invariants (secret masking, excluded-file disclosure,
   untrusted repository configuration). A prepared-copy wrapper fixes all three in this test, but
   OCR's repository-level configuration surface can grow between releases, so every upgrade would
   need this evaluation again.
2. The isolation used here is macOS-only. The Linux container (Render, CI) has no equivalent
   profile yet; a per-run network namespace with an egress proxy that allows only the provider
   host would be needed before running it in production.
3. Its output has no severity, confidence or evidence class, and anchors are resolved from quoted
   code; refactorX would re-verify every comment with its own anchor check anyway.
4. Review quality and cost against refactorX's own path are unmeasured: both need a live
   provider (blocked until the owner configures one). The 55 MB binary per platform also adds to
   the image.

## Conditions for re-evaluation

Run `crp-dev ocr-eval` on the new version; add a Linux isolation profile; feed only the prepared
copy with a per-run `HOME`; normalise comments into `ai_findings` through the same anchor
verifier; compare quality and tokens with `crp-dev ai-eval --live` on the same model and the
held-out split.
