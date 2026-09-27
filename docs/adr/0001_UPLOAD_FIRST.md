# ADR 0001 — Upload-first immutable source snapshots

Status: accepted specification, 26 September 2026.

Context: the user wants a first release accepting source uploads or an explicitly selected directory, without requiring repository-host integration.

Decision: ZIP and user-invoked local folder capture are required Phase 1 sources. All modes produce the same immutable manifest/snapshot. Git metadata is optional. Browser folder selection and registered server sources are optional adapters; arbitrary server path/URL reads are excluded.

Consequences: findings, graphs and fixes use snapshot identity, not mandatory commit SHA. Non-Git users can export patches in P05. Local capture transmits source in its initial mode and must disclose this. Private execution and Git connectors arrive later.

Alternatives: GitHub-first onboarding adds unnecessary account dependency; an arbitrary path field in a hosted app cannot safely provide laptop/server access.

