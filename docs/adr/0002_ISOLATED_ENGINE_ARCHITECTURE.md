# ADR 0002 — Modular control plane and isolated replaceable engines

Status: accepted specification, 26 September 2026.

Decision: own data/coverage/graph/finding contracts and UI; reuse pinned scanners behind adapters. Use a modular API with durable workflows and isolated workers. Begin with PostgreSQL and artifact storage rather than many specialized databases.

Consequences: tool failures remain visible; adapters need output/coverage contract tests; exact component/rule licensing must be reviewed. AI consumes bounded evidence and cannot grant itself tool permissions. Local container isolation is not automatically a hosted hostile multi-tenant boundary.

Alternatives: forking an entire scanner UI binds the product to its capabilities/licensing; one unbounded agent scanning a whole repository cannot provide reliable coverage or cost control.

