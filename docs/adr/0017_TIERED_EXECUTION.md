# ADR 0017 — Tiered execution for compile, build and test (Tier 0 first)

Status: accepted. Tier 0 (TypeScript type-check, no execution) is implemented and verified. Tiers
1 and 2 wait for the owner's decision on paid isolated compute. Date: 9 October 2026. Owner:
repository owner (prompts/P09_ISOLATED_BUILD.md); implemented by the development agent.

Context:

- Reviewers want to know whether their fixes still compile. Compiling usually means running
  build tools, and build tools run project code: Maven plugins and extensions, Gradle scripts,
  annotation processors, npm lifecycle scripts. Uploaded code is hostile until proven otherwise.
- The free and lite deployments have no isolation beyond a bounded child process. Hardware-isolated
  sandboxes (microVMs or gVisor) cost money and are an owner decision.
- Results must be bound to exact content, and anything not run must say so.

Decision:

1. **Tier 0 — read-only checks (now).**
   - The platform's pinned TypeScript compiler (5.9.3, in `engines/eslint-runner`, owned and
     locked by the platform) type-checks the TypeScript files and the root `tsconfig.json`.
   - It reads them as data, in a bounded child process (time, output and heap limits) with a
     scrubbed environment.
   - The compiler host only sees the checked folder and the compiler's own standard library.
   - From the project's configuration, it never uses: compiler plugins and transformers, automatic
     `@types`, a project-installed TypeScript, configuration that `extends` files outside the
     folder, output paths, or emitted files.
   - Errors caused by packages that are not installed (uploads exclude `node_modules`) are counted
     separately and are not reported as project errors.
   - Java's Tier 0 remains the parse check that already exists (Tree-sitter in the P05 ladder).
     `javac` is Tier 1.
2. **Where Tier 0 runs.**
   - Fix workspace checks type-check the upload's and the changed copy's TypeScript files.
   - They report errors the changes introduced and removed. Errors are matched by file, code and
     message, so moved lines do not count.
   - Results are cached in the artifact store by checker identity and the exact (path, content
     hash) of every input, so the same content always gives the same answer.
   - More than `CRP_TYPECHECK_MAX_FILES` files (3,000) are skipped with a plain reason;
     `CRP_TYPECHECK_TIMEOUT_SECONDS` defaults to 300.
3. **Tier 1 — safe compile in a sandbox (needs the owner's compute decision).**
   - Java: `javac --release N -proc:none` against dependencies resolved from coordinates without
     build extensions.
   - TypeScript/JavaScript: `npm ci --ignore-scripts` then `tsc --noEmit` with real type packages.
   - Network only to an allow-listed package proxy.
4. **Tier 2 — full build and tests (needs hardware isolation).**
   - Only in microVM- or gVisor-class sandboxes, with no network by default, no credentials,
     an ephemeral filesystem, resource caps and allow-listed commands.
   - Bring-your-own CI through P06 pull requests, and customer runners, are alternatives.
5. **Honesty.** Every surface says what ran: "TypeScript is type-checked; nothing is built, run or
   tested, and Java is not compiled." Tiers that are not available are never shown as passed.

Consequences:

- Fix workspaces now show new and fixed TypeScript type errors, a real compile-level signal for
  TypeScript projects, at no infrastructure cost and with no code execution.
- The type-check cannot see types from packages that are not installed. Code that depends on them
  is checked less strictly (imports become `any`), and the count of such imports is shown.
- Java compile, builds and tests stay "not run" until Tier 1 and 2 infrastructure exists (K-P09-01).

Alternatives considered:

- Running `tsc` from the project's own `node_modules`, or `npx tsc`: rejected, because it executes
  project-controlled code.
- Installing dependencies to get full types: that is Tier 1 (`npm ci --ignore-scripts` in a
  sandbox), not Tier 0.
- Reporting missing-module errors as project errors: rejected, because they would drown real
  errors in noise.
