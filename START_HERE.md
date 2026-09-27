# Start developing the Code Review Platform

Prepared 26 September 2026. This is a development specification and prompt kit, not a working application. All implementation phases start as NOT_STARTED.

**Status update (26 September 2026):** Phase 0 is implemented and its gate passed on macOS; see [docs/PHASE_STATUS.md](docs/PHASE_STATUS.md) and [docs/validation/P00_REPORT.md](docs/validation/P00_REPORT.md). For a new session use `prompts/RESUME.md`; the next phase prompt is `prompts/P01_SOURCE_AND_BASELINE.md`.

## Recommended use

1. Extract this kit into a new development directory. Keep the files in its root; do not paste the entire ZIP into an agent conversation.
2. Open that directory in Codex or Claude Code. Use the normal permission settings of your environment.
3. Send the launch message below. It asks the agent to bootstrap the application and finish Phase 0.
4. Then run one phase prompt at a time from `prompts/`. Each phase must have implementation and validation evidence before the next depends on it.
5. For a new session, use `prompts/RESUME.md`. The agent checks the code, status and handoff rather than rebuilding completed work.

## Launch message

```text
Read MASTER_DEVELOPMENT_PROMPT.md and follow it as the project specification.
Read AGENTS.md, docs/PHASE_STATUS.md and docs/memory/PROJECT_STATE.md.
Inspect the current directory and preserve existing user work.
Use relevant available skills and tools; discover their actual capabilities first.
Start implementation now. Complete Phase 0 using prompts/P00_FOUNDATION.md,
including working application scaffolding, database migrations, real health checks,
development scripts and meaningful validation. Do not stop after writing a plan.
Record results, limitations and the next runnable task in the project memory.
Do not mark anything complete without evidence from this workspace.
```

If you only want to copy one document, paste `MASTER_DEVELOPMENT_PROMPT.md` into Codex or Claude Code in an empty project directory. It contains the complete product direction, document-generation instructions and phase roadmap. The included files provide more implementation detail when available.

For continuous local development after you have reviewed the scope, use this alternative launch instruction:

```text
Follow MASTER_DEVELOPMENT_PROMPT.md. Implement Phases 0 through 5 in order,
using each phase prompt and passing its mandatory gates before depending on it.
Do not stop after planning or ask for routine implementation choices.
Checkpoint progress in the documented memory files and resume from actual state.
If an external credential, platform environment or host limitation blocks a gate,
record the precise blocker and continue independent authorized work without
pretending that blocked validation passed. Do not publish or deploy externally.
```

## Prompt sequence

| Order | Prompt | Milestone |
|---|---|---|
| 0 | `prompts/P00_FOUNDATION.md` | Executable development foundation |
| 1 | `prompts/P01_SOURCE_AND_BASELINE.md` | Upload/folder intake, inventory, real baseline analysis and dashboard |
| 2 | `prompts/P02_GRAPH_AND_ANALYZERS.md` | Persistent mappings and additional deterministic analysis |
| 3 | `prompts/P03_AGENTIC_ANALYSIS.md` | Evidence-backed AI investigation and repository Q&A |
| 4 | `prompts/P04_ENTERPRISE_FRAMEWORKS.md` | SAP Commerce and Salesforce specialization |
| 5 | `prompts/P05_VALIDATED_FIXES.md` | Patch generation, validation and download |
| 6 | `prompts/P06_GIT_AND_INCREMENTAL.md` | Git integration, incremental scans and pull requests |
| 7 | `prompts/P07_PRODUCTION_HARDENING.md` | Qualified scale, security, operations and release |

Use `prompts/PHASE_AUDIT.md` to independently check a milestone, `prompts/REPAIR_FAILED_GATE.md` for failed checks, and `prompts/RELEASE_REVIEW.md` before a production readiness claim.

## Initial source modes

- ZIP upload is required in Phase 1.
- A local CLI runner snapshots a folder explicitly selected on the user's machine. The default Phase 1 mode uploads the snapshot for analysis and must say so.
- A browser folder picker is an optional convenience with capability detection and ZIP fallback. It transmits selected files; it does not grant arbitrary filesystem access.
- An administrator may register a server-mounted read-only directory. The UI selects an opaque registered source ID, never an arbitrary server path.
- Git remote integration arrives in Phase 6. It is not a prerequisite for the first usable product.

An archive has a content-derived snapshot ID; it need not have a Git commit. Never invent a commit SHA.

## What to configure during development

The agent should select compatible supported versions and record them in `docs/TOOLCHAIN.md`. Phase 0/1 must run without model credentials. AI features become available only after provider configuration and an explicit data-egress policy. Platform-specific compilation/testing may require licensed SAP dependencies or a suitable Salesforce org; unavailable prerequisites remain visible blockers for those capabilities.

No external plugin is mandatory for bootstrap. Project-local commands are the fallback. Do not install every available plugin, embed credentials in Markdown, enable unrestricted tool execution, or claim planned commands already work.

Read `docs/CONTEXT_AND_MEMORY.md` for the lightweight daily workflow and `docs/SKILLS_AND_PLUGINS.md` for tool selection.
