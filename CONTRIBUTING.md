# Contributing

Read AGENTS.md, active phase and relevant architecture decision records. Keep changes scoped to a feature, defect or gate. Preserve working-tree changes and do not rewrite unrelated history.

Before sending a change run `make check` and `make test` (and `make test-e2e` for UI/flow changes); after API model changes run `make contracts`; after model changes add an Alembic migration (see docs/INSTALLATION.md). Add meaningful positive and negative tests at changed trust boundaries. Run the relevant checks and report unavailable dependencies. API/schema changes need migrations and consumer updates; engine changes need contract fixtures, coverage behavior and license/version review.

PR descriptions should explain the problem, resulting behavior, validation and limitations. Include UI evidence for visible changes, and never attach real customer source or secrets. Do not claim new language/framework support without a capability matrix and fixtures.

Use compact factual handoffs for multi-session work. If parallel engineering is authorized, assign disjoint file ownership and integrate through reviewed contracts; concurrency does not remove final integration responsibility.
