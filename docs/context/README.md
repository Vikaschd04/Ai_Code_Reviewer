# Scoped context packets

This folder contains context-generation guidance, not customer source or generated startup memory.

Phase 0 should implement bounded development context utilities; see CONTEXT_AND_MEMORY.md. Generated packets belong in an ignored artifact/cache location. Include task ID, trusted root/worktree digest, selected paths/hashes, relevant contracts/ADRs, source spans, recent check summary and missing/truncated scope.

Do not auto-import packets through CLAUDE.md or concatenate every document. Load one task packet when useful and revalidate changed hashes. Customer analysis uses a separate authorized retrieval system described in AI_ORCHESTRATION.md.


Implemented commands (P00): `make context-map` → `.local/context/map-<digest>.json`; `make context-pack TASK=<id> PATHS="<approved paths>"` → `.local/context/packs/<task>/<key>.md`. Budgets: `--max-files`/`--max-symbols` (map), `--max-bytes`/`--max-file-bytes` (pack). Packets record the worktree digest, per-file SHA-256, phase status rows, the latest JUnit summary and an explicit truncation section; they are reused only while all inputs are unchanged.
