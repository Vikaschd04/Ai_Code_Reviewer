# Playbook — Extend framework support

Read FRAMEWORK_ADAPTERS.md, current capability matrix and the version-specific official sources needed for the task.

Choose one bounded relationship or rule family. Define version detection, required metadata, unsupported cases and evidence classification. Implement parsing/mapping without executing config; use a domain engine where appropriate. Add positive/negative and missing-metadata fixtures. Verify generated code and dynamic wiring are handled conservatively.

Update capabilities and docs only for actual supported behavior. Mark runtime/build validation conditional until its environment was exercised. Obtain SME review for strong production claims. This is a project playbook, not an installed skill.

