# NFR code-pattern fixture

Synthetic Java and TypeScript for the code-pattern rules (P12 slice 3): outgoing calls without
timeouts, blocking calls in reactive code and unbounded thread pools. `Unsafe*` files hold the
positive examples; `Safe*` files the negatives. `packages/analysis/tests/test_security_engines.py`
(`test_resilience_rules_fire_only_on_positive_examples`) holds the expected lines.
