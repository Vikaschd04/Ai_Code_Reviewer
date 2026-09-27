# Repair a failed implementation gate

Read current phase status, failed validation report, relevant code and the phase prompt. Identify the exact failed invariant and reproduce it with the smallest meaningful check.

Implement the underlying correction while preserving existing behavior and user work. Do not disable tests, weaken rules, replace real integrations with mocks or relabel failed work as optional. Rerun the focused check and any impacted regression/phase gate. If an external dependency is unavailable, implement correct unavailable handling and record the still-blocked integration validation.

Update validation evidence, known issues, phase status and handoff. Report the root cause, behavior change, checks/results and remaining blockers concisely.

