# Execute Phase 8 — Fix workspace: change sets, manual and AI fixes, compare, export

Read P05_REPORT, ADR 0014, ADR 0012 (AI), ADR 0015 (GitHub), UI_SPEC, SECURITY_MODEL and
research/MARKET_ANALYSIS_2026.md §A. Users must be able to fix many issues inside refactorX and
take the result away without Git hosting. GitHub remains an optional delivery channel.

## Deliver

1. **Change sets.** A per-project working copy bound to one base snapshot (an upload or a commit).
   - It holds revisions of any number of files. Each change records its provenance (recipe,
     manual, AI), its author, and the findings it addresses.
   - States: draft, checking, ready, exported.
   - Optimistic versions protect concurrent edits; the original upload is never modified.
2. **Editing in the portal.**
   - An accessible code editor (CodeMirror 6 is preferred; confirm version and license) with
     syntax highlighting for Java, JavaScript/TypeScript, XML, JSON, Apex and properties.
     Binary files are not editable.
   - The fix policy (P05) runs on every save.
     - Suppressions, weakened tests and oversized changes are *flagged*. A finding cleared by
       suppression is never counted as fixed.
     - Manual configuration edits (for example a dependency upgrade in `pom.xml`) are allowed and
       labelled.
   - Files can be added or deleted; deletions are listed explicitly.
3. **Fixing issues in bulk.** Select one or many issues and choose:
   - **Apply recipe:** deterministic, including "all occurrences of this rule".
   - **Ask AI:** bounded AI patches through the P03 provider and the project AI policy, with up
     to N candidate fixes, labelled AI, passing the same policy and P05 validation.
   - **Fix by hand:** opens the editor at the line.

   Overlapping edits on the same lines are a conflict the user resolves; they are never silently
   merged. Budgets: files per change set, changed lines, AI calls and spend.
4. **Re-check the change set.** Analyze a derived snapshot (base + edits) with per-file reuse.
   - Show per issue: fixed (verified absent), still reported, not rechecked, plus any new
     findings introduced.
   - Show compile and type results where P09 is available; otherwise say "not compiled".
   - Results are bound to the change-set content hash.
5. **Compare.**
   - Simplified Git-like views per file and per change set: side by side and inline, unchanged
     regions collapsed, an ignore-whitespace option, word-level highlights.
   - Compare any two uploads or commits of a project (files added, removed, changed and renamed,
     each with its diff).
   - Code is shown as inert text.
6. **Export.**
   - Exports:
     - one Git-compatible patch for all files (`git apply` / `git am` with a message);
     - a ZIP of **only the changed files** with their folders plus a manifest;
     - a ZIP of the full patched project;
     - a change summary (JSON and Markdown: what was fixed and how it was verified, what was not,
       known risks);
     - for GitHub-connected projects, one pull request for the whole change set (P06 freshness
       rules).
   - Every export names its base snapshot and hashes, and warns that it applies only to that
     exact base.
7. **UI.**
   - A workspace page: issue queue with filters, file tree with change markers, editor, compare,
     check results, export.
   - The finding page links into the workspace.
   - Plain language; technical details collapsed. Checked in light, dark and mobile.

## Mandatory tests

- **Export correctness.** A multi-file change set's patch applies with `git apply --check` on a
  copy of the exact base and fails on a changed copy. The changed-files ZIP contains exactly the
  changed files and paths. The full ZIP equals base + edits.
- **Provenance and honesty.**
  - Recipe, manual and AI changes keep their provenance.
  - A suppression edit is flagged and the finding is not counted as fixed.
  - A weakened test is flagged.
- **Conflicts and limits.** Overlapping edits are detected. Binary, oversized and invalid-encoding
  files are refused for editing. CRLF and Unicode are preserved.
- **Re-check.** It reports fixed, still present and new findings correctly, including new
  findings in unchanged files caused by configuration edits.
- **AI.** Off when the policy is off. Candidates are labelled and validated. Budget exhaustion is
  honest. Prompt injection from source stays data.
- **Safety.** The original upload is unchanged (digest). Other workspaces get 404. Script and HTML
  in code are rendered inert in the editor and diff.
- **Real stack.** API, worker (Temporal and lite) and browser E2E for the full journey: select
  issues → fix (recipe, manual, AI with the labelled fake model) → re-check → compare → export →
  apply the patch.

## Completion

Produce P08_REPORT.md, update API/DATA/UI/SECURITY docs, user docs and state. AI-candidate quality
claims need the live provider (otherwise BLOCKED for that part only). Compile results depend on P09
and are shown as "not compiled" until then.
