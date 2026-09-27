# File guide

Read only the files relevant to the current task. This guide is a navigation aid, not a request to load all documentation.

| Need | Start with |
|---|---|
| Begin a new project | START_HERE.md → MASTER_DEVELOPMENT_PROMPT.md → prompts/P00_FOUNDATION.md |
| Agent behavior | AGENTS.md; CLAUDE.md is the Claude bridge |
| Current work and next action | docs/PHASE_STATUS.md; docs/memory/PROJECT_STATE.md; SESSION_HANDOFF.md |
| Product scope and phases | docs/PROJECT_BRIEF.md; FEATURE_MATRIX.md; ROADMAP.md; BACKLOG.md |
| Source upload/folder capture | docs/SOURCE_INTAKE.md; API_CONTRACTS.md |
| Architecture and persistence | docs/ARCHITECTURE.md; DATA_MODEL.md; docs/adr/ |
| Analysis and mappings | docs/ANALYSIS_PIPELINE.md; FRAMEWORK_ADAPTERS.md |
| AI/provider/retrieval | docs/AI_ORCHESTRATION.md; STANDARDS_REGISTRY.md |
| UI implementation | docs/UI_SPEC.md; API_CONTRACTS.md |
| Engineering conventions | docs/CODING_STANDARDS.md; CONTRIBUTING.md |
| Memory/context/token efficiency | docs/CONTEXT_AND_MEMORY.md; docs/context/README.md |
| Skills and plugins | docs/SKILLS_AND_PLUGINS.md; docs/playbooks/ |
| Dependencies and commercial reuse | docs/ENGINE_ADOPTION.md; TOOLCHAIN.md; REFERENCES.md |
| Security and operations | SECURITY.md; docs/SECURITY_MODEL.md; OPERATIONS.md |
| Install/run/test | docs/INSTALLATION.md; TEST_STRATEGY.md; DEFINITION_OF_DONE.md |
| Validate a milestone | prompts/PHASE_AUDIT.md; docs/validation/PHASE_REPORT_TEMPLATE.md |
| Resume or repair | prompts/RESUME.md; REPAIR_FAILED_GATE.md |
| Production readiness | prompts/P07_PRODUCTION_HARDENING.md; RELEASE_REVIEW.md |
| Original research background | docs/research/ORIGINAL_RESEARCH.md; newer intake decisions override its Git-first assumption |

Application code now exists for Phase 0 (`apps/`, `services/`, `packages/`, `tools/`, `Makefile`); see README.md for the layout and docs/INSTALLATION.md for commands. Later-phase directories, commands and reports remain requirements until their phase report exists.
