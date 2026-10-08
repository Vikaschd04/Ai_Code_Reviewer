# Execute Phase 9 — Compile and build in the portal (isolated runner)

Read research/MARKET_ANALYSIS_2026.md §A ("Compiling in the portal"), SECURITY_MODEL, ADR 0014
(validation ladder), FRAMEWORK_ADAPTERS (validation profiles) and P08_REPORT. This phase completes
the P05 tests and build steps and the P08 "compile" capability. Uploaded code is hostile until
proven otherwise.

## Deliver

1. **Tiered execution model.** Shown to users in plain words.
   - **Tier 0, no execution:**
     - syntax, already present;
     - TypeScript type-check with the TypeScript compiler, which loads no project code (missing
       dependency types are reported as such);
     - Java parse.
   - **Tier 1, safe compile in a sandbox:**
     - Java: `javac --release N -proc:none` against dependencies resolved by a resolver that never
       runs build extensions (for example Maven Resolver or Coursier reading POM coordinates,
       parents and BOMs). Gradle only when a lockfile or version catalog provides coordinates;
       otherwise "not available".
     - TypeScript/JavaScript: `npm ci --ignore-scripts` (or an equivalent with lifecycle scripts
       off) and `tsc --noEmit`.
     - Network only to an allowlisted package proxy or cache; no secrets.
   - **Tier 2, full build and tests (runs project code):**
     - Only in a hardware-isolated microVM (Firecracker-class, for example self-hosted E2B) or a
       gVisor sandbox with equivalent controls. No network by default (package proxy only), no
       credentials, ephemeral filesystem, CPU, memory, process and time caps, output limits.
     - Allow-listed commands per ecosystem (for example `mvn -o -B test` after offline
       resolution, `npm test`), never free-form shell from an agent.
   - **Bring your own runner:**
     - the customer's CI through a P06 pull request or branch (read check runs and statuses as
       evidence);
     - or a customer-hosted runner agent in their network (design with P07).
   - **Platform profiles:**
     - Salesforce: check-only deploy and Apex tests in a customer-authorized scratch or sandbox
       org; credentials encrypted and scoped (with P07).
     - SAP Commerce: customer runner with a licensed suite only.
2. **Results.** Compiler diagnostics mapped to file and line. Test results (JUnit/XML, Jest) are
   parsed and summarized. Every result is bound to the snapshot or change-set content hash and the
   toolchain version. Unavailable tiers say why.
3. **Integration.**
   - P05 ladder: tests and build steps become "passed / failed / not run (reason)".
   - P08 change sets: compile on demand.
   - Fix pull requests: show CI results when available.
4. **Operations.** Sandbox images pinned by digest with SBOMs. The package proxy cache is
   integrity-checked. Per-workspace quotas and queues; cost ceilings. Requires paid compute: the
   owner chooses managed or self-hosted. The free plan shows "not available here".

## Mandatory tests

- **Code that must not run:** a malicious annotation processor (Tier 1, `-proc:none`), an npm
  `postinstall` script, a Maven extension, a Gradle script.
- **Containment in Tier 2:** egress blocked; fork and memory bombs contained; timeout kill;
  `ptrace`, mount and raw sockets refused; no host files visible; environment holds no secrets;
  cleanup after every run.
- **Correct results:** missing dependency and offline resolution failure are honest outcomes;
  compile errors are mapped correctly; flaky-test reruns are bounded and reported.
- **Binding:** results are bound to hashes; a stale change set invalidates them.
- **Customer CI:** results read from a test repository through the P06 fake and live GitHub.

## Completion

Produce P09_REPORT.md with an isolation assessment (what is proven, what is not), runbooks and cost
measurements. Infrastructure unavailable or not approved means BLOCKED for the affected tiers.
Never report a tier as passed without running it.
