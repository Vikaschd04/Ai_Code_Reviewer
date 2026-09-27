# Coding standards

## Shared rules

Prefer cohesive modules, explicit contracts and composition over speculative abstraction. Keep business rules outside routes/UI. Use descriptive identifiers, small understandable functions, documented invariants and minimal duplication. Do not add dead scaffolding or generic frameworks for hypothetical future languages.

Validate external inputs at boundaries; represent domain states with enums/tagged structures rather than loosely shaped dictionaries. Use dependency injection at integration seams. Configuration is typed, documented and validated on startup. Keep defaults in one authoritative location.

Use UTC storage with timezone-aware timestamps, consistent identifiers and bounded pagination. Preserve causal exceptions; classify retryable/fatal/user errors; redact secrets/absolute source paths. No swallowed exceptions, broad success fallbacks, TODO-only engine implementations or fake data in real scans.

## Python

Select a supported Python baseline and exact tooling in TOOLCHAIN.md. Use type hints, Pydantic boundary models, Ruff formatting/linting and a selected strict type checker. Manage dependencies with a lockfile. Async APIs must not perform blocking extraction/parsing in the request loop; delegate to workers. Use subprocess argument arrays, fixed approved executables and bounded I/O; never shell-interpolate repository/model values.

SQLAlchemy repositories/services have explicit transactions and workspace predicates. Alembic owns schema changes. Avoid unbounded ORM collection loads and N+1 API serialization. Use resource managers and cancellation cleanup.

## TypeScript and UI

Enable strict TypeScript. Use generated/validated API types; no broad any or unchecked assertions to bypass type errors. Keep state ownership clear; use accessible components, labelled controls, keyboard navigation and focus management. Treat source text and model Markdown as untrusted display content. Never expose provider/runner tokens to bundles or log them in the browser.

Use bounded/virtualized lists and graph neighborhoods. Represent loading, empty, partial, denied, canceled and failed states explicitly. Abort obsolete requests and avoid displaying results from a previously selected snapshot.

## Tests and dependencies

Tests cover behavior and concrete risk, not implementation mirroring. Fixtures are synthetic and labelled. A scanner adapter needs positive/negative, crash, timeout and malformed-output tests. Never remove assertions, weaken policies or suppress failures to finish a phase.

Pin compatible dependency/tool/image versions and verify them against official documentation. Record license and maintenance decisions. Use approved update PRs and regression checks rather than floating latest tags in released workers. Public interfaces need migration notes when changed.

## Definition of clean code here

Readable, typed, bounded, observable and testable code that enforces the source/data boundary. Formatting is one dimension; correctness, evidence integrity and safe failure are release requirements.

