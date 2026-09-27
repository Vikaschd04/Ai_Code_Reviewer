# Playbook — Checkpoint and resume

At a durable task boundary or before context compaction: record changed paths, completed task IDs, actual checks/results, unresolved issues and exact next task. Update compact PROJECT_STATE and SESSION_HANDOFF; store long evidence in validation artifacts.

At resume: read root instructions, status, state and handoff; inspect current worktree; verify any state that matters for the next edit. Load only the active phase/contracts/code. Reuse verified work, invalidate stale assumptions and avoid restarting completed phases.

Never save secrets, customer source or private reasoning. Token counts are actual only when exposed by the tool. This is a playbook, not an installed skill.

