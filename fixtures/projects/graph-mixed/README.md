# Synthetic fixture: graph-mixed

Labelled synthetic project for graph extraction/resolution tests (no real code, no secrets).
Two Maven modules (`core`, `app`), an npm workspace (`web` depending on `shared`), a
`tsconfig.json` with comments and a `paths` alias, re-exports, a dynamic non-literal import,
an undeclared package, an import of a type that does not exist, and one file with a syntax
error. `docs/validation/P02_REPORT.md` lists the expected classifications.
