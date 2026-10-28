# ADR 0027 — Dependency upgrades as fixes

Status: accepted; implemented and verified (unit tests, real-stack fix and check, browser
journey). Date: 28 October 2026. Owner: repository owner ("work on the next in the queue",
28 October 2026; backlog ADD-A2 from docs/research/USER_CHALLENGES_AND_ADDONS_2026.md).

Context and constraints:

- Vulnerable libraries are the most common high-severity finding in uploads (for example
  `log4j-core` 2.14.1 in the sample project). Trivy names the package, the installed version and
  the versions that fix each vulnerability, but users still had to edit the build file by hand.
- The fix policy refuses any change to analyzer or build configuration (`pom.xml`,
  `package.json`, `build.gradle`, ...). That rule exists so a fix can never hide a finding, for
  example by excluding a rule in a build plugin's settings.
- AGENTS.md:
  - Source-only analysis is not build or runtime verification.
  - Never execute uploaded code or run package managers on it.
  - Do not weaken rules to make fixes pass.

Decision:

1. **Maven recipe `deps:maven-upgrade`** (`crp_analysis/fixes/dependency_recipes.py`). It applies
   to a Trivy vulnerability of a Maven library (`group:artifact`) in a `pom.xml` that has a
   published fix.
   - **Target version:** for each vulnerability of that library and version in the upload, take
     the lowest fixed version newer than the installed one. Prefer the same major version, and
     never take a pre-release. Then take the highest of those, so one change fixes them all.
     Versions are ordered like Maven: `2.17` = `2.17.0` = `2.17.0.RELEASE`, `1.0-rc1` < `1.0`.
   - **The only edit:** the version value, where the file sets it. That is the dependency's
     `<version>`, its `<dependencyManagement>` entry, or the `<properties>` entry its `${...}`
     names. A shared property is upgraded, and the fix says the other libraries move too.
   - **Never guessed:** a version from a parent POM or BOM, a library not declared in the file,
     a build plugin's own dependency, or a version that is not a plain number.
2. **A narrow policy exception.** A change to `pom.xml` passes the build-configuration rule only
   when every changed line is a single element whose version value changed:
   - a `<version>` element, or
   - an element inside `<properties>`.

   Both the old and the new value must look like versions, and the number of lines must stay the
   same. Everything else in a build file is still refused, as before.
3. **npm is fixed by hand.** `package-lock.json` entries carry integrity hashes that only the
   registry can provide, and refactorX does not run package managers. These findings keep
   Trivy's recommendation ("Upgrade lodash from 4.17.15 to a fixed version (4.17.21)").
4. **Verification:**
   - The workspace check re-runs Trivy on the copy and reports each vulnerability as fixed or
     still present.
   - Compiling and running the project's tests remain "not run" (P09 Tiers 1–2, owner's compute).
     Every upgrade says so in its note.

Alternatives considered:

- **OpenRewrite or Maven Versions plugin:** both need a JVM build of the project, and so do
  Renovate and Dependabot, which also need repository hosting. That is P09 Tiers 1–2.
- **Deleting the lockfile entry's integrity:** rejected. It weakens supply-chain protection.
- **One finding, one version:** rejected. Log4Shell's follow-ups would each move the version
  again, and every further fix would conflict.

Consequences:

- Fixing one vulnerability of a library fixes them all at once. In bulk, the remaining findings
  of the same library are recorded as fixed by the same change.
- A new major version is labelled "(new major version)" in the fix's title.
- Reversal: drop the recipe and the policy exception; nothing else depends on them.

Evidence: docs/validation/P08_REPORT.md (dependency upgrades section). Tests:
`packages/analysis/tests/test_dependency_recipes.py`, the policy tests, the real-stack workspace
check and `e2e/fix-checkpoint.spec.ts`.
