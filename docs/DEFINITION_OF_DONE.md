# Definition of done

A task is done when its intended behavior works in the actual implementation, inputs/errors are handled, meaningful checks pass, relevant docs match, and remaining conditional limitations are explicit.

A phase is COMPLETE only when every mandatory acceptance gate in its prompt has evidence. Report commands, environment, results and artifacts; do not infer a passed test from a file name or code inspection. Missing dependencies, credentials or host capabilities are BLOCKED/UNAVAILABLE outcomes for affected gates.

Required across phases: correct authorization/snapshot identity; input and output validation; accurate coverage/failure states; no fake findings; bounded execution; relevant regression tests; UI inspection for changed flows; coherent installation/configuration; updated status/backlog/handoff.

For fixes, evidence must attach to the exact patch/base/result snapshot. For AI, claims must anchor to real source and approved standards; model agreement is not proof. For production, capacity, isolation, recovery and operations must be tested, not merely documented.

A planned endpoint, interface, TODO, skipped test, mock response or successful build alone does not establish feature completion. A local documentation kit audit validates only the kit.

